"""SQLAlchemy adapter for Identity's profile and onboarding persistence port."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.application.contracts import (
    IdentityStore,
    OnboardingDraft,
    PreferencesSnapshot,
    ProcessingAuthorization,
    ProcessingConsentSnapshot,
)
from pin.contexts.identity.application.processing_consent import ProcessingConsentConflict
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.models import (
    OnboardingRecord,
    PreferencesRecord,
    ProcessingConsentRecord,
)
from pin.persistence.receipts import lock_action
from pin.platform.models import InboxUpdate


class SqlAlchemyIdentityStore(IdentityStore):
    """Keep profile and draft writes in the caller's shared unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None:
        record = await self._session.get(PreferencesRecord, owner_id)
        return _snapshot(owner_id, record) if record else None

    async def lock_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None:
        """Refresh and lock the preference row until the caller ends this transaction."""
        record = await self._session.scalar(
            select(PreferencesRecord)
            .where(PreferencesRecord.owner_id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _snapshot(owner_id, record) if record else None

    async def begin_onboarding(
        self,
        *,
        owner_id: UUID,
        mode: str,
        initial_values: dict[str, object],
        base_profile_version: int | None,
    ) -> OnboardingDraft:
        await self._session.execute(
            insert(OnboardingRecord)
            .values(
                owner_id=owner_id,
                draft_id=uuid4(),
                mode=mode,
                step="language",
                draft=initial_values,
                base_profile_version=base_profile_version,
                version=1,
            )
            .on_conflict_do_nothing(index_elements=[OnboardingRecord.owner_id])
        )
        record = await self._session.scalar(
            select(OnboardingRecord).where(OnboardingRecord.owner_id == owner_id).with_for_update()
        )
        if record is None:
            raise RuntimeError("onboarding draft was not visible in the current transaction")
        if record.step == "complete":
            result = await self._session.execute(
                update(OnboardingRecord)
                .where(
                    OnboardingRecord.owner_id == owner_id,
                    OnboardingRecord.version == record.version,
                    OnboardingRecord.step == "complete",
                )
                .values(
                    draft_id=uuid4(),
                    mode=mode,
                    step="language",
                    draft=initial_values,
                    base_profile_version=base_profile_version,
                    version=OnboardingRecord.version + 1,
                )
                .returning(OnboardingRecord)
            )
            refreshed = result.scalar_one_or_none()
            if refreshed is None:
                raise RuntimeError("completed onboarding draft changed while starting an edit")
            record = refreshed
        return _draft(record)

    async def get_onboarding(self, owner_id: UUID) -> OnboardingDraft | None:
        record = await self._session.scalar(
            select(OnboardingRecord).where(OnboardingRecord.owner_id == owner_id)
        )
        return _draft(record) if record else None

    async def advance_onboarding(
        self,
        draft: OnboardingDraft,
        *,
        step: str,
        values: dict[str, object],
    ) -> OnboardingDraft | None:
        result = await self._session.execute(
            update(OnboardingRecord)
            .where(
                OnboardingRecord.owner_id == draft.owner_id,
                OnboardingRecord.draft_id == draft.draft_id,
                OnboardingRecord.version == draft.version,
                OnboardingRecord.step == draft.step,
            )
            .values(step=step, draft=values, version=OnboardingRecord.version + 1)
            .returning(OnboardingRecord.version)
        )
        version = result.scalar_one_or_none()
        return (
            OnboardingDraft(
                draft.owner_id,
                draft.draft_id,
                draft.mode,
                step,
                values,
                draft.base_profile_version,
                int(version),
            )
            if version is not None
            else None
        )

    async def cancel_onboarding(self, owner_id: UUID) -> None:
        await self._session.execute(
            delete(OnboardingRecord).where(OnboardingRecord.owner_id == owner_id)
        )

    async def save_preferences(
        self,
        *,
        owner_id: UUID,
        expected_version: int | None,
        preferences: Preferences,
    ) -> PreferencesSnapshot | None:
        values = _preference_values(preferences)
        if expected_version is None:
            result = await self._session.execute(
                insert(PreferencesRecord)
                .values(owner_id=owner_id, **values)
                .on_conflict_do_nothing(index_elements=[PreferencesRecord.owner_id])
                .returning(PreferencesRecord.owner_id)
            )
        else:
            result = await self._session.execute(
                update(PreferencesRecord)
                .where(
                    PreferencesRecord.owner_id == owner_id,
                    PreferencesRecord.version == expected_version,
                )
                .values(**values)
                .returning(PreferencesRecord.owner_id)
            )
        if result.scalar_one_or_none() is None:
            return None
        saved = await self._session.get(PreferencesRecord, owner_id, populate_existing=True)
        if saved is None:
            raise RuntimeError("saved preferences were not visible in the current transaction")
        return _snapshot(owner_id, saved)

    async def complete_onboarding(
        self,
        draft: OnboardingDraft,
        *,
        values: dict[str, object],
        profile_version: int,
    ) -> bool:
        result = await self._session.execute(
            update(OnboardingRecord)
            .where(
                OnboardingRecord.owner_id == draft.owner_id,
                OnboardingRecord.draft_id == draft.draft_id,
                OnboardingRecord.version == draft.version,
                OnboardingRecord.step == "confirm",
            )
            .values(
                step="complete",
                draft=values,
                base_profile_version=profile_version,
                version=OnboardingRecord.version + 1,
            )
            .returning(OnboardingRecord.owner_id)
        )
        return result.scalar_one_or_none() is not None


    async def lock_processing_consent(self, owner_id: UUID) -> None:
        await lock_action(self._session, owner_id, "identity.processing_consent")

    async def get_processing_consent(self, owner_id: UUID) -> ProcessingConsentSnapshot:
        record = await self._session.scalar(
            select(ProcessingConsentRecord)
            .where(ProcessingConsentRecord.owner_id == owner_id)
            .execution_options(populate_existing=True)
        )
        if record is None:
            return ProcessingConsentSnapshot(owner_id=owner_id, state="required", revision=0)
        return _consent_snapshot(record)

    async def grant_processing_consent(
        self,
        *,
        owner_id: UUID,
        expected_revision: int,
        policy_version: str,
        accepted_at: datetime,
        language: str,
        telegram_user_id: int,
        inbox_update_id: UUID,
    ) -> ProcessingConsentSnapshot:
        from pin.contexts.identity.application.processing_consent import (
            PROCESSING_CONSENT_POLICY_VERSION,
        )

        if policy_version != PROCESSING_CONSENT_POLICY_VERSION:
            raise ProcessingConsentConflict("processing consent policy is stale")
        if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
            raise ValueError("processing consent time must be timezone-aware")
        await self.lock_processing_consent(owner_id)
        current = await self.get_processing_consent(owner_id)
        if current.revision != expected_revision:
            raise ProcessingConsentConflict("processing consent revision changed")
        inbox = await self._session.get(InboxUpdate, inbox_update_id)
        if inbox is None or inbox.owner_id != owner_id or not _inbox_actor_matches(
            inbox.payload, telegram_user_id
        ):
            raise ProcessingConsentConflict("processing consent update is not owner-bound")
        record = await self._session.get(ProcessingConsentRecord, owner_id)
        values = {
            "state": "granted",
            "revision": expected_revision + 1,
            "policy_version": policy_version,
            "grant_id": uuid4(),
            "accepted_at": accepted_at,
            "accepted_language": language,
            "accepted_telegram_user_id": telegram_user_id,
            "accepted_update_id": inbox_update_id,
            "revoked_at": None,
        }
        if record is None:
            record = ProcessingConsentRecord(owner_id=owner_id, **values)
            self._session.add(record)
        else:
            for name, value in values.items():
                setattr(record, name, value)
        await self._session.flush()
        return _consent_snapshot(record)

    async def revoke_processing_consent(
        self,
        *,
        owner_id: UUID,
        expected_revision: int,
        expected_grant_id: UUID,
        expected_policy_version: str,
        revoked_at: datetime,
    ) -> ProcessingConsentSnapshot:
        if revoked_at.tzinfo is None or revoked_at.utcoffset() is None:
            raise ValueError("processing consent time must be timezone-aware")
        await self.lock_processing_consent(owner_id)
        current = await self.get_processing_consent(owner_id)
        if current.revision != expected_revision:
            raise ProcessingConsentConflict("processing consent revision changed")
        if (
            current.state == "revoked"
            and current.grant_id == expected_grant_id
            and current.policy_version == expected_policy_version
        ):
            return current
        if (
            current.state != "granted"
            or current.grant_id != expected_grant_id
            or current.policy_version != expected_policy_version
        ):
            raise ProcessingConsentConflict("processing consent grant is no longer current")
        record = await self._session.get(ProcessingConsentRecord, owner_id)
        if record is None:
            raise ProcessingConsentConflict("processing consent record disappeared")
        record.state = "revoked"
        record.revision += 1
        record.revoked_at = revoked_at
        await self._session.flush()
        return _consent_snapshot(record)

    async def authorize_processing_consent(
        self, owner_id: UUID, authorization: ProcessingAuthorization
    ) -> bool:
        await self.lock_processing_consent(owner_id)
        current = await self.get_processing_consent(owner_id)
        return (
            authorization.owner_id == owner_id
            and current.state == "granted"
            and current.grant_id == authorization.grant_id
            and current.policy_version == authorization.policy_version
        )

    async def invalidate_restored_processing_consent(self, owner_id: UUID) -> bool:
        await self.lock_processing_consent(owner_id)
        record = await self._session.get(ProcessingConsentRecord, owner_id)
        if record is None or record.state != "granted":
            return False
        record.state = "required"
        record.revision += 1
        record.policy_version = None
        record.grant_id = None
        record.accepted_at = None
        record.accepted_language = None
        record.accepted_telegram_user_id = None
        record.accepted_update_id = None
        record.revoked_at = None
        await self._session.flush()
        return True

    async def list_granted_processing_consent_owners(self) -> tuple[UUID, ...]:
        rows = await self._session.scalars(
            select(ProcessingConsentRecord.owner_id)
            .where(ProcessingConsentRecord.state == "granted")
            .order_by(ProcessingConsentRecord.owner_id)
        )
        return tuple(rows)


def _snapshot(owner_id: UUID, record: PreferencesRecord) -> PreferencesSnapshot:
    return PreferencesSnapshot(
        owner_id=owner_id,
        language=record.language,
        home_zone=record.home_zone,
        reminder_calendar_days=record.reminder_calendar_days,
        reminder_elapsed_hours=record.reminder_elapsed_hours,
        quiet_start=record.quiet_start,
        quiet_end=record.quiet_end,
        review_time=record.review_time,
        version=record.version,
    )


def _draft(record: OnboardingRecord) -> OnboardingDraft:
    return OnboardingDraft(
        owner_id=record.owner_id,
        draft_id=record.draft_id,
        mode=record.mode,
        step=record.step,
        values=dict(record.draft),
        base_profile_version=record.base_profile_version,
        version=record.version,
    )


def _preference_values(preferences: Preferences) -> dict[str, object]:
    return {
        "language": preferences.language,
        "home_zone": preferences.home_zone,
        "reminder_calendar_days": preferences.reminder_calendar_days,
        "reminder_elapsed_hours": preferences.reminder_elapsed_hours,
        "quiet_start": preferences.quiet_start,
        "quiet_end": preferences.quiet_end,
        "review_time": preferences.review_time,
        "version": preferences.version,
    }


def _consent_snapshot(record: ProcessingConsentRecord) -> ProcessingConsentSnapshot:
    return ProcessingConsentSnapshot(
        owner_id=record.owner_id,
        state=record.state,
        revision=record.revision,
        policy_version=record.policy_version,
        grant_id=record.grant_id,
        accepted_at=record.accepted_at,
        accepted_language=record.accepted_language,
        accepted_telegram_user_id=record.accepted_telegram_user_id,
        accepted_update_id=record.accepted_update_id,
        revoked_at=record.revoked_at,
    )


def _inbox_actor_matches(payload: object, telegram_user_id: int) -> bool:
    if not isinstance(payload, dict):
        return False
    callback = payload.get("callback_query")
    message = callback.get("message") if isinstance(callback, dict) else payload.get("message")
    actor = callback.get("from") if isinstance(callback, dict) else (
        message.get("from") if isinstance(message, dict) else None
    )
    chat = message.get("chat") if isinstance(message, dict) else None
    return (
        isinstance(actor, dict)
        and actor.get("id") == telegram_user_id
        and actor.get("is_bot") is not True
        and isinstance(chat, dict)
        and chat.get("id") == telegram_user_id
    )
