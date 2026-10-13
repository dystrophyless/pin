"""Published, immutable preference snapshots and zone-change handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol
from uuid import UUID

from pin.contexts.billing.api import TrialActivationResult
from pin.contexts.identity.domain.preferences import Preferences


@dataclass(frozen=True, slots=True)
class PreferencesSnapshot:
    owner_id: UUID
    language: str
    home_zone: str
    reminder_calendar_days: int
    reminder_elapsed_hours: int
    quiet_start: time
    quiet_end: time
    review_time: time
    version: int


@dataclass(frozen=True, slots=True)
class HomeZoneChangeHandoff:
    """A downstream mutations consumer can preview before shifting saved plans."""

    owner_id: UUID
    previous_home_zone: str
    requested_home_zone: str
    preferences_version: int
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class OnboardingDraft:
    owner_id: UUID
    draft_id: UUID
    mode: str
    step: str
    values: dict[str, object]
    base_profile_version: int | None
    version: int


class IdentityStore(Protocol):
    """Preference and resumable-draft persistence behind the Identity context."""

    async def get_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None: ...

    async def begin_onboarding(
        self,
        *,
        owner_id: UUID,
        mode: str,
        initial_values: dict[str, object],
        base_profile_version: int | None,
    ) -> OnboardingDraft: ...

    async def get_onboarding(self, owner_id: UUID) -> OnboardingDraft | None: ...

    async def advance_onboarding(
        self,
        draft: OnboardingDraft,
        *,
        step: str,
        values: dict[str, object],
    ) -> OnboardingDraft | None: ...

    async def cancel_onboarding(self, owner_id: UUID) -> None: ...

    async def save_preferences(
        self,
        *,
        owner_id: UUID,
        expected_version: int | None,
        preferences: Preferences,
    ) -> PreferencesSnapshot | None: ...

    async def complete_onboarding(
        self, draft: OnboardingDraft, *, values: dict[str, object], profile_version: int
    ) -> bool: ...


class TrialActivator(Protocol):
    async def activate_trial(
        self, *, owner_id: UUID, telegram_user_id: int, at: datetime
    ) -> TrialActivationResult: ...
