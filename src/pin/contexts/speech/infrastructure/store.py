"""Lease-fenced durable transcripts and continuation receipts."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.api import SqlAlchemyIdentityStore
from pin.contexts.speech.application.contracts import SpeechRecognition
from pin.contexts.speech.domain.audio import AudioFacts
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.platform.jobs import JobClaim, lock_job_for_effects


class SpeechRequestConflict(RuntimeError):
    """A stable speech job key was replayed with different source facts."""


class SqlAlchemySpeechRequestStore:
    async def initialize(
        self,
        session: AsyncSession,
        claim: JobClaim,
        *,
        inbox_id: UUID,
        request_key: str,
        declared_size_bytes: int | None,
        declared_duration_seconds: float,
        at: datetime,
    ) -> SpeechRequestRecord:
        _validate_claim(claim)
        if claim.owner_id is None:
            raise SpeechRequestConflict("speech job has no owner")
        await SqlAlchemyIdentityStore(session).lock_processing_consent(claim.owner_id)
        await lock_job_for_effects(session, claim)
        row = await session.get(SpeechRequestRecord, claim.job_id, with_for_update=True)
        if row is not None:
            if (
                row.owner_id != claim.owner_id
                or row.inbox_id != inbox_id
                or row.request_key != request_key
                or row.declared_size_bytes != declared_size_bytes
                or row.declared_duration_seconds != declared_duration_seconds
                or row.processing_grant_id != claim.processing_grant_id
                or row.processing_policy_version != claim.processing_policy_version
            ):
                raise SpeechRequestConflict("speech job was replayed with different input facts")
            return row
        row = SpeechRequestRecord(
            job_id=claim.job_id,
            owner_id=claim.owner_id,
            inbox_id=inbox_id,
            processing_grant_id=claim.processing_grant_id,
            processing_policy_version=claim.processing_policy_version,
            request_key=request_key,
            state="processing",
            declared_size_bytes=declared_size_bytes,
            declared_duration_seconds=declared_duration_seconds,
            actual_size_bytes=None,
            decoded_duration_seconds=None,
            transcript=None,
            language=None,
            failure_code=None,
            created_at=at,
            updated_at=at,
        )
        session.add(row)
        await session.flush()
        return row

    async def get(self, session: AsyncSession, claim: JobClaim) -> SpeechRequestRecord | None:
        _validate_claim(claim)
        return cast(
            SpeechRequestRecord | None,
            await session.scalar(
                select(SpeechRequestRecord).where(
                    SpeechRequestRecord.job_id == claim.job_id,
                    SpeechRequestRecord.owner_id == claim.owner_id,
                )
            ),
        )

    async def store_facts(
        self,
        session: AsyncSession,
        claim: JobClaim,
        *,
        audio: AudioFacts,
        at: datetime,
    ) -> None:
        row = await self._lock_request(session, claim)
        if row.state == "transcribed":
            return
        if row.state != "processing":
            raise SpeechRequestConflict("speech request is no longer processing")
        row.actual_size_bytes = audio.size_bytes
        row.decoded_duration_seconds = audio.duration_seconds
        row.updated_at = at

    async def store_transcript(
        self,
        session: AsyncSession,
        claim: JobClaim,
        *,
        recognition: SpeechRecognition,
        at: datetime,
    ) -> SpeechRequestRecord:
        row = await self._lock_request(session, claim)
        if row.state == "transcribed":
            if (
                row.transcript != recognition.text
                or row.language != recognition.language
                or row.decoded_duration_seconds != recognition.audio.duration_seconds
            ):
                raise SpeechRequestConflict("speech transcript replay changed its result")
            return row
        if row.state != "processing":
            raise SpeechRequestConflict("speech request is no longer processing")
        row.actual_size_bytes = recognition.audio.size_bytes
        row.decoded_duration_seconds = recognition.audio.duration_seconds
        row.transcript = recognition.text
        row.language = recognition.language
        row.state = "transcribed"
        row.failure_code = None
        row.updated_at = at
        return row

    async def mark_continued(self, session: AsyncSession, claim: JobClaim, *, at: datetime) -> None:
        row = await self._lock_request(session, claim)
        if row.state == "continued":
            return
        if row.state != "transcribed":
            raise SpeechRequestConflict("only a durable transcript can continue capture")
        row.state = "continued"
        row.transcript = None
        row.language = None
        row.updated_at = at

    async def mark_failure(
        self,
        session: AsyncSession,
        claim: JobClaim,
        *,
        code: str,
        at: datetime,
        rejected: bool,
    ) -> None:
        if not code or len(code) > 64:
            raise ValueError("speech failure code must contain 1 to 64 characters")
        row = await self._lock_request(session, claim)
        if row.state in {"continued", "failed", "rejected"}:
            return
        row.state = "rejected" if rejected else "failed"
        row.transcript = None
        row.language = None
        row.failure_code = code
        row.updated_at = at

    async def _lock_request(self, session: AsyncSession, claim: JobClaim) -> SpeechRequestRecord:
        _validate_claim(claim)
        if claim.owner_id is None:
            raise SpeechRequestConflict("speech job has no owner")
        await SqlAlchemyIdentityStore(session).lock_processing_consent(claim.owner_id)
        await lock_job_for_effects(session, claim)
        row = await session.scalar(
            select(SpeechRequestRecord)
            .where(
                SpeechRequestRecord.job_id == claim.job_id,
                SpeechRequestRecord.owner_id == claim.owner_id,
            )
            .with_for_update()
        )
        if row is None:
            raise SpeechRequestConflict("speech request record is missing")
        if (
            row.processing_grant_id != claim.processing_grant_id
            or row.processing_policy_version != claim.processing_policy_version
        ):
            raise SpeechRequestConflict("speech transcript consent provenance changed")
        return row


def _validate_claim(claim: JobClaim) -> None:
    if claim.owner_id is None or claim.lane.value != "speech":
        raise ValueError("speech request requires an owner-bound speech job")
