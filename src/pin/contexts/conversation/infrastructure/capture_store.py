"""Short-UoW persistence adapters for text capture and exact confirmation proofs."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.conversation.application.contracts import CaptureBegin, CaptureProofPart
from pin.contexts.conversation.domain.capture import (
    CaptureConflict,
    CaptureDraft,
    CaptureMessage,
    CaptureState,
    append_user_input,
    start_capture,
)
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureConfirmationRecord,
    CaptureDraftRecord,
    CaptureInputRecord,
    CaptureSequenceRecord,
)
from pin.contexts.planning.api import (
    ConfirmationGrant,
    ConfirmationProof,
    RecurrenceConfirmationGrant,
    RecurrenceConfirmationProof,
)


class SqlAlchemyCaptureStore:
    """Share one session with the caller's transaction; never opens a UoW itself."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Session for trusted proof adapters that must share this caller UoW."""
        return self._session

    async def begin(
        self,
        *,
        owner_id: UUID,
        input_key: str,
        text: str,
        at: datetime,
        expires_at: datetime,
    ) -> CaptureBegin:
        _require_instant(at)
        if owner_id.int == 0 or not input_key or len(input_key) > 128:
            raise ValueError("capture owner or input key is invalid")
        existing = await self._session.scalar(
            select(CaptureInputRecord).where(
                CaptureInputRecord.owner_id == owner_id,
                CaptureInputRecord.input_key == input_key,
            )
        )
        if existing is not None:
            draft_record = await self._session.scalar(
                select(CaptureDraftRecord)
                .where(
                    CaptureDraftRecord.owner_id == owner_id,
                    CaptureDraftRecord.id == existing.draft_id,
                )
                .with_for_update()
            )
            if draft_record is None:
                raise CaptureConflict("capture input no longer has a draft")
            return CaptureBegin(
                draft=_draft(draft_record),
                input_state=existing.state,
                response=existing.response,
                is_new_input=False,
            )

        sequence_stmt = (
            pg_insert(CaptureSequenceRecord)
            .values(owner_id=owner_id, last_sequence=1)
            .on_conflict_do_update(
                index_elements=[CaptureSequenceRecord.owner_id],
                set_={
                    "last_sequence": CaptureSequenceRecord.last_sequence + 1,
                },
            )
            .returning(CaptureSequenceRecord.last_sequence)
        )
        sequence = cast(int, await self._session.scalar(sequence_stmt))
        active_record = await self._session.scalar(
            select(CaptureDraftRecord)
            .where(
                CaptureDraftRecord.owner_id == owner_id,
                CaptureDraftRecord.state.in_(
                    (
                        CaptureState.COLLECTING.value,
                        CaptureState.PROCESSING.value,
                        CaptureState.READY.value,
                    )
                ),
            )
            .with_for_update()
        )
        if active_record is not None and at >= active_record.expires_at:
            active_record.state = CaptureState.EXPIRED.value
            active_record.version += 1
            active_record.updated_at = at
            active_record.normalized_actions = None
            active_record.preview = None
            active_record.projection_sha256 = None
            active_record.processing_key = None
            active_record = None
        if active_record is None:
            draft = start_capture(
                draft_id=uuid4(),
                owner_id=owner_id,
                text=text,
                request_key=input_key,
                at=at,
                expires_at=expires_at,
                sequence=sequence,
            )
            record = _record(draft)
            self._session.add(record)
        else:
            previous = _draft(active_record)
            if previous.state is CaptureState.PROCESSING:
                raise CaptureConflict("another capture turn is still processing")
            draft = append_user_input(previous, text=text, request_key=input_key, at=at)
            _apply(active_record, draft)
        receipt = CaptureInputRecord(
            id=uuid4(),
            owner_id=owner_id,
            draft_id=draft.id,
            input_key=input_key,
            sequence=sequence,
            state="processing",
            created_at=at,
            updated_at=at,
        )
        self._session.add(receipt)
        await self._session.flush()
        return CaptureBegin(draft, "processing", None, True)

    async def persist_draft(self, draft: CaptureDraft, *, expected_version: int) -> None:
        record = await self._session.scalar(
            select(CaptureDraftRecord)
            .where(
                CaptureDraftRecord.id == draft.id,
                CaptureDraftRecord.owner_id == draft.owner_id,
                CaptureDraftRecord.version == expected_version,
            )
            .with_for_update()
        )
        if record is None:
            raise CaptureConflict("capture draft changed during processing")
        _apply(record, draft)
        await self._session.flush()

    async def get_draft(self, *, owner_id: UUID, draft_id: UUID) -> CaptureDraft | None:
        record = await self._session.scalar(
            select(CaptureDraftRecord)
            .where(
                CaptureDraftRecord.owner_id == owner_id,
                CaptureDraftRecord.id == draft_id,
            )
            .with_for_update()
        )
        return _draft(record) if record is not None else None

    async def revoke_proofs(self, *, owner_id: UUID, draft_id: UUID, at: datetime) -> None:
        rows = await self._session.scalars(
            select(CaptureConfirmationRecord)
            .where(
                CaptureConfirmationRecord.owner_id == owner_id,
                CaptureConfirmationRecord.draft_id == draft_id,
                CaptureConfirmationRecord.consumed_at.is_(None),
            )
            .with_for_update()
        )
        for row in rows:
            row.consumed_at = at
        await self._session.flush()

    async def complete_input(
        self,
        *,
        owner_id: UUID,
        input_key: str,
        state: str,
        response: dict[str, object],
        at: datetime,
    ) -> None:
        if state not in {"complete", "failed", "unknown"}:
            raise ValueError("capture input completion state is invalid")
        record = await self._session.scalar(
            select(CaptureInputRecord)
            .where(
                CaptureInputRecord.owner_id == owner_id,
                CaptureInputRecord.input_key == input_key,
            )
            .with_for_update()
        )
        if record is None or record.state != "processing":
            raise CaptureConflict("capture input receipt is stale")
        record.state = state
        record.response = response
        record.updated_at = at
        await self._session.flush()

    async def issue_proofs(
        self,
        *,
        owner_id: UUID,
        draft_id: UUID,
        preview_version: int,
        parts: tuple[CaptureProofPart, ...],
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        _require_instant(issued_at)
        _require_instant(expires_at)
        if not parts or expires_at <= issued_at:
            raise ValueError("capture confirmation proofs need parts and a future expiry")
        if len({part.key for part in parts}) != len(parts):
            raise ValueError("capture proof part keys must be unique")
        credential = secrets.token_urlsafe(24)
        credential_sha256 = hashlib.sha256(credential.encode("ascii")).hexdigest()
        for part in parts:
            if len(part.projection_sha256) != 64 or not part.key or len(part.key) > 32:
                raise ValueError("capture proof part is invalid")
            self._session.add(
                CaptureConfirmationRecord(
                    id=part.preview_id,
                    owner_id=owner_id,
                    draft_id=draft_id,
                    preview_version=preview_version,
                    part_key=part.key,
                    credential_sha256=credential_sha256,
                    projection_sha256=part.projection_sha256,
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
            )
        await self._session.flush()
        return credential

    async def rotate_proofs(
        self, *, owner_id: UUID, draft_id: UUID, preview_version: int, at: datetime
    ) -> str:
        """Reissue an ephemeral callback credential after restart without changing preview."""
        _require_instant(at)
        rows = list(
            (
                await self._session.scalars(
                    select(CaptureConfirmationRecord)
                    .where(
                        CaptureConfirmationRecord.owner_id == owner_id,
                        CaptureConfirmationRecord.draft_id == draft_id,
                        CaptureConfirmationRecord.preview_version == preview_version,
                    )
                    .with_for_update()
                )
            ).all()
        )
        if not rows or any(row.consumed_at is not None or at >= row.expires_at for row in rows):
            raise CaptureConflict("capture confirmation credential has expired")
        credential = secrets.token_urlsafe(24)
        digest = hashlib.sha256(credential.encode("ascii")).hexdigest()
        for row in rows:
            row.credential_sha256 = digest
        await self._session.flush()
        return credential

    async def locate_confirmation(
        self, *, owner_id: UUID, credential: str, at: datetime
    ) -> tuple[UUID, int] | None:
        """Resolve a high-entropy callback token without embedding IDs in Telegram data."""
        _require_instant(at)
        if not credential or len(credential) > 64:
            return None
        if not credential.isascii():
            return None
        digest = hashlib.sha256(credential.encode("ascii")).hexdigest()
        rows = list(
            (
                await self._session.scalars(
                    select(CaptureConfirmationRecord).where(
                        CaptureConfirmationRecord.owner_id == owner_id,
                        CaptureConfirmationRecord.credential_sha256 == digest,
                    )
                )
            ).all()
        )
        identities = {(row.draft_id, row.preview_version) for row in rows}
        if len(identities) != 1:
            return None
        draft_id, version = next(iter(identities))
        draft = await self._session.scalar(
            select(CaptureDraftRecord)
            .where(
                CaptureDraftRecord.id == draft_id,
                CaptureDraftRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if (
            draft is None
            or draft.state != CaptureState.READY.value
            or draft.version != version
            or at >= draft.expires_at
            or any(row.consumed_at is not None for row in rows)
            or any(not row.issued_at <= at < row.expires_at for row in rows)
        ):
            return None
        return draft_id, version


class _CaptureConfirmationVerifier:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _verify(
        self,
        preview_id: UUID,
        preview_version: int,
        credential: str,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> CaptureConfirmationRecord | None:
        _require_instant(at)
        if not credential or len(credential) > 64 or not credential.isascii():
            return None
        row = await self._session.scalar(
            select(CaptureConfirmationRecord)
            .where(
                CaptureConfirmationRecord.id == preview_id,
                CaptureConfirmationRecord.owner_id == owner_id,
                CaptureConfirmationRecord.preview_version == preview_version,
            )
            .with_for_update()
        )
        if (
            row is None
            or row.consumed_at is not None
            or row.projection_sha256 != approved_projection_sha256
            or not hmac.compare_digest(
                row.credential_sha256, hashlib.sha256(credential.encode("ascii")).hexdigest()
            )
            or not row.issued_at <= at < row.expires_at
        ):
            return None
        row.consumed_at = at
        await self._session.flush()
        return row


class SqlAlchemyCaptureConfirmationVerifier(_CaptureConfirmationVerifier):
    """One-off verifier for capture proofs; consumes in Planning's shared UoW."""

    async def verify_and_consume(
        self,
        proof: ConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> ConfirmationGrant | None:
        row = await self._verify(
            proof.preview_id,
            proof.preview_version,
            proof.credential,
            owner_id=owner_id,
            approved_projection_sha256=approved_projection_sha256,
            at=at,
        )
        return _one_off_grant(row) if row is not None else None


class SqlAlchemyCaptureRecurrenceConfirmationVerifier(_CaptureConfirmationVerifier):
    """Recurring verifier for capture proofs; shares the confirmed command UoW."""

    async def verify_and_consume(
        self,
        proof: RecurrenceConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> RecurrenceConfirmationGrant | None:
        row = await self._verify(
            proof.preview_id,
            proof.preview_version,
            proof.credential,
            owner_id=owner_id,
            approved_projection_sha256=approved_projection_sha256,
            at=at,
        )
        return _recurrence_grant(row) if row is not None else None


def _draft(record: CaptureDraftRecord) -> CaptureDraft:
    return CaptureDraft(
        id=record.id,
        owner_id=record.owner_id,
        version=record.version,
        state=CaptureState(record.state),
        messages=tuple(CaptureMessage(item["role"], item["text"]) for item in record.messages),
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
        sequence=record.sequence,
        processing_key=record.processing_key,
        normalized_actions=tuple(record.normalized_actions or ()),
        preview=record.preview,
        projection_sha256=record.projection_sha256,
        confirmed_result=record.confirmed_result,
    )


def _record(draft: CaptureDraft) -> CaptureDraftRecord:
    return CaptureDraftRecord(
        id=draft.id,
        owner_id=draft.owner_id,
        version=draft.version,
        sequence=draft.sequence,
        state=draft.state.value,
        messages=[{"role": message.role, "text": message.text} for message in draft.messages],
        normalized_actions=list(draft.normalized_actions) or None,
        preview=draft.preview,
        projection_sha256=draft.projection_sha256,
        processing_key=draft.processing_key,
        confirmed_result=draft.confirmed_result,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        expires_at=draft.expires_at,
    )


def _apply(record: CaptureDraftRecord, draft: CaptureDraft) -> None:
    record.version = draft.version
    record.sequence = draft.sequence
    record.state = draft.state.value
    record.messages = [{"role": message.role, "text": message.text} for message in draft.messages]
    record.normalized_actions = list(draft.normalized_actions) or None
    record.preview = draft.preview
    record.projection_sha256 = draft.projection_sha256
    record.processing_key = draft.processing_key
    record.confirmed_result = draft.confirmed_result
    record.updated_at = draft.updated_at


def _one_off_grant(row: CaptureConfirmationRecord) -> ConfirmationGrant:
    return ConfirmationGrant(
        owner_id=row.owner_id,
        preview_id=row.id,
        preview_version=row.preview_version,
        producer_id="conversation.text_capture",
        approved_projection_sha256=row.projection_sha256,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
    )


def _recurrence_grant(row: CaptureConfirmationRecord) -> RecurrenceConfirmationGrant:
    return RecurrenceConfirmationGrant(
        owner_id=row.owner_id,
        preview_id=row.id,
        preview_version=row.preview_version,
        producer_id="conversation.text_capture",
        approved_projection_sha256=row.projection_sha256,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
    )


def _require_instant(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capture times must be timezone-aware")
