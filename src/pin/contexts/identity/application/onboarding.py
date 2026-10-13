"""Durable onboarding and preference-preview commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID

from pin.contexts.billing.api import TrialActivationResult
from pin.contexts.identity.application.contracts import (
    HomeZoneChangeHandoff,
    IdentityStore,
    PreferencesSnapshot,
    TrialActivator,
)
from pin.contexts.identity.domain.preferences import Preferences, validate_home_zone


class OnboardingStep:
    LANGUAGE = "language"
    HOME_ZONE = "home_zone"
    REMINDERS = "reminders"
    QUIET_REVIEW = "quiet_review"
    CONFIRM = "confirm"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    state: str
    step: str | None = None
    mode: str | None = None
    preferences: PreferencesSnapshot | None = None
    home_zone_change: HomeZoneChangeHandoff | None = None
    draft: dict[str, object] | None = None
    trial: TrialActivationResult | None = None


def _draft_values(preferences: PreferencesSnapshot) -> dict[str, object]:
    return {
        "language": preferences.language,
        "home_zone": preferences.home_zone,
        "reminder_calendar_days": preferences.reminder_calendar_days,
        "reminder_elapsed_hours": preferences.reminder_elapsed_hours,
        "quiet_start": preferences.quiet_start.isoformat(),
        "quiet_end": preferences.quiet_end.isoformat(),
        "review_time": preferences.review_time.isoformat(),
    }


def _preferences_from_draft(draft: dict[str, object], version: int) -> Preferences:
    return Preferences(
        language=str(draft.get("language", "")),
        home_zone=validate_home_zone(str(draft.get("home_zone", ""))),
        reminder_calendar_days=_int_value(draft.get("reminder_calendar_days"), -1),
        reminder_elapsed_hours=_int_value(draft.get("reminder_elapsed_hours"), -1),
        quiet_start=time.fromisoformat(str(draft.get("quiet_start", ""))),
        quiet_end=time.fromisoformat(str(draft.get("quiet_end", ""))),
        review_time=time.fromisoformat(str(draft.get("review_time", ""))),
        version=version,
    )


def _int_value(value: object, default: int) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


async def get_preferences(store: IdentityStore, owner_id: UUID) -> PreferencesSnapshot | None:
    return await store.get_preferences(owner_id)


async def begin_onboarding(
    store: IdentityStore, *, owner_id: UUID, now: datetime
) -> OnboardingResult:
    _require_aware(now)
    profile = await store.get_preferences(owner_id)
    if profile is not None:
        return OnboardingResult("existing_profile", preferences=profile)
    draft = await store.begin_onboarding(
        owner_id=owner_id,
        mode="initial",
        initial_values={},
        base_profile_version=None,
    )
    if draft.step == OnboardingStep.COMPLETE:
        return OnboardingResult(
            "existing_profile", preferences=await store.get_preferences(owner_id)
        )
    return OnboardingResult(
        "started" if draft.version == 1 else "resumed",
        draft.step,
        draft.mode,
        draft=dict(draft.values),
    )


async def begin_preferences_edit(
    store: IdentityStore, *, owner_id: UUID, now: datetime
) -> OnboardingResult:
    profile = await store.get_preferences(owner_id)
    if profile is None:
        return await begin_onboarding(store, owner_id=owner_id, now=now)
    draft = await store.begin_onboarding(
        owner_id=owner_id,
        mode="preferences",
        initial_values=_draft_values(profile),
        base_profile_version=profile.version,
    )
    return OnboardingResult(
        "started" if draft.version == 1 else "resumed",
        draft.step,
        draft.mode,
        draft=dict(draft.values),
    )


async def cancel_onboarding(store: IdentityStore, *, owner_id: UUID) -> OnboardingResult:
    draft = await store.get_onboarding(owner_id)
    await store.cancel_onboarding(owner_id)
    return OnboardingResult(
        "cancelled",
        mode=draft.mode if draft else None,
        preferences=await store.get_preferences(owner_id),
        draft=dict(draft.values) if draft else None,
    )


async def answer_onboarding(store: IdentityStore, *, owner_id: UUID, text: str) -> OnboardingResult:
    normalized = text.strip()
    if normalized.casefold() in {"/cancel", "cancel", "отмена"}:
        return await cancel_onboarding(store, owner_id=owner_id)

    draft = await store.get_onboarding(owner_id)
    if draft is None:
        return OnboardingResult("not_in_progress")
    if draft.step == OnboardingStep.COMPLETE:
        return OnboardingResult(
            "already_complete", preferences=await store.get_preferences(owner_id)
        )
    if draft.step == OnboardingStep.CONFIRM:
        if normalized.casefold() not in {"confirm", "yes", "да", "подтверждаю"}:
            return OnboardingResult(
                "confirm_required", draft.step, draft.mode, draft=dict(draft.values)
            )
        return OnboardingResult(
            "ready_to_confirm", draft.step, draft.mode, draft=dict(draft.values)
        )

    parsed = _apply_answer(draft.step, normalized, dict(draft.values), draft.mode)
    if parsed is None:
        return OnboardingResult("invalid_input", draft.step, draft.mode, draft=dict(draft.values))
    values = dict(draft.values)
    values.update(parsed)
    next_step = {
        OnboardingStep.LANGUAGE: OnboardingStep.HOME_ZONE,
        OnboardingStep.HOME_ZONE: OnboardingStep.REMINDERS,
        OnboardingStep.REMINDERS: OnboardingStep.QUIET_REVIEW,
        OnboardingStep.QUIET_REVIEW: OnboardingStep.CONFIRM,
    }[draft.step]
    advanced = await store.advance_onboarding(draft, step=next_step, values=values)
    if advanced is None:
        return OnboardingResult("draft_conflict", draft.step, draft.mode, draft=dict(draft.values))
    return OnboardingResult("advanced", next_step, advanced.mode, draft=dict(advanced.values))


async def confirm_onboarding(
    store: IdentityStore,
    trial_activator: TrialActivator,
    *,
    owner_id: UUID,
    telegram_user_id: int,
    now: datetime,
) -> OnboardingResult:
    _require_aware(now)
    draft = await store.get_onboarding(owner_id)
    if draft is None:
        return OnboardingResult("not_in_progress")
    if draft.step == OnboardingStep.COMPLETE:
        return OnboardingResult(
            "already_complete", preferences=await store.get_preferences(owner_id)
        )
    if draft.step != OnboardingStep.CONFIRM:
        return OnboardingResult("confirmation_not_ready", draft.step, draft.mode)

    current = await store.get_preferences(owner_id)
    if (current.version if current else None) != draft.base_profile_version:
        await store.cancel_onboarding(owner_id)
        return OnboardingResult(
            "version_conflict", draft.step, draft.mode, draft=dict(draft.values)
        )
    next_version = 1 if current is None else current.version + 1
    preferences = _preferences_from_draft(draft.values, next_version)
    saved = await store.save_preferences(
        owner_id=owner_id,
        expected_version=draft.base_profile_version,
        preferences=preferences,
    )
    if saved is None:
        await store.cancel_onboarding(owner_id)
        return OnboardingResult(
            "version_conflict", draft.step, draft.mode, draft=dict(draft.values)
        )

    previous_zone = current.home_zone if current else None
    trial = None
    if draft.mode == "initial":
        trial = await trial_activator.activate_trial(
            owner_id=owner_id,
            telegram_user_id=telegram_user_id,
            at=now.astimezone(UTC),
        )
    if not await store.complete_onboarding(
        draft, values=_draft_values(saved), profile_version=saved.version
    ):
        raise RuntimeError(
            "onboarding draft changed during confirmation; roll back the unit of work"
        )
    handoff = (
        HomeZoneChangeHandoff(
            owner_id, previous_zone, saved.home_zone, saved.version, now.astimezone(UTC)
        )
        if previous_zone is not None and previous_zone != saved.home_zone
        else None
    )
    return OnboardingResult("confirmed", preferences=saved, home_zone_change=handoff, trial=trial)


def _apply_answer(
    step: str, text: str, draft: dict[str, object], mode: str
) -> dict[str, object] | None:
    if mode == "preferences" and text.casefold() in {"keep", "same", "оставить"}:
        return {}
    if step == OnboardingStep.LANGUAGE:
        language = {
            "en": "en",
            "english": "en",
            "английский": "en",
            "ru": "ru",
            "russian": "ru",
            "русский": "ru",
        }.get(text.casefold())
        return {"language": language} if language else None
    if step == OnboardingStep.HOME_ZONE:
        try:
            return {"home_zone": validate_home_zone(text)}
        except ValueError:
            return None
    if step == OnboardingStep.REMINDERS:
        if text.casefold() in {"suggested", "default", "по умолчанию", "предложенные"}:
            return {"reminder_calendar_days": 1, "reminder_elapsed_hours": 1}
        match = re.fullmatch(
            r"\s*(\d+)\s*(?:days?|calendar\s+days?)?\s*[,; ]\s*"
            r"(\d+)\s*(?:hours?|час(?:а|ов)?)?\s*",
            text.casefold(),
        )
        if match:
            return {
                "reminder_calendar_days": int(match.group(1)),
                "reminder_elapsed_hours": int(match.group(2)),
            }
        return None
    if step == OnboardingStep.QUIET_REVIEW:
        if text.casefold() in {"suggested", "default", "по умолчанию", "предложенные"}:
            return {"quiet_start": "22:00:00", "quiet_end": "08:00:00", "review_time": "20:00:00"}
        match = re.fullmatch(
            r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*[,; ]\s*(\d{1,2}:\d{2})\s*", text
        )
        if not match:
            return None
        try:
            start, end, review = (time.fromisoformat(value) for value in match.groups())
            if start == end:
                return None
        except ValueError:
            return None
        return {
            "quiet_start": start.isoformat(),
            "quiet_end": end.isoformat(),
            "review_time": review.isoformat(),
        }
    return None


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("onboarding timestamps must be timezone-aware")
