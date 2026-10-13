"""SQLAlchemy adapter for Identity's profile and onboarding persistence port."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.application.contracts import (
    IdentityStore,
    OnboardingDraft,
    PreferencesSnapshot,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord


class SqlAlchemyIdentityStore(IdentityStore):
    """Keep profile and draft writes in the caller's shared unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None:
        record = await self._session.get(PreferencesRecord, owner_id)
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
