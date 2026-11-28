"""Initial preference defaults and durable preference previews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID
from zoneinfo import available_timezones

from pin.contexts.identity.application.contracts import (
    HomeZoneChangeHandoff,
    IdentityStore,
    PreferencesSnapshot,
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


async def initialize_preferences(
    store: IdentityStore, *, owner_id: UUID, language: str
) -> PreferencesSnapshot:
    """Create defaults once under the caller's owner fence."""
    profile = await store.save_preferences(
        owner_id=owner_id,
        expected_version=None,
        preferences=Preferences(language=language, home_zone="UTC"),
    )
    if profile is not None:
        draft = await store.get_onboarding(owner_id)
        if draft is not None and draft.mode == "initial":
            await store.cancel_onboarding(owner_id)
        return profile
    profile = await store.get_preferences(owner_id)
    if profile is None:
        raise RuntimeError("initial preferences were not visible after insert conflict")
    return profile


async def begin_preferences_edit(
    store: IdentityStore, *, owner_id: UUID
) -> OnboardingResult:
    profile = await store.get_preferences(owner_id)
    if profile is None:
        return OnboardingResult("not_in_progress")
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
    draft = await store.get_onboarding(owner_id)
    if draft is None or draft.mode != "preferences" or draft.step == OnboardingStep.COMPLETE:
        return OnboardingResult("not_in_progress")
    normalized = text.strip()
    if normalized.casefold() in {"/cancel", "cancel", "отмена"}:
        return await cancel_onboarding(store, owner_id=owner_id)

    if draft.step == OnboardingStep.CONFIRM:
        if normalized.casefold() not in {"confirm", "yes", "да", "подтверждаю"}:
            return OnboardingResult(
                "confirm_required", draft.step, draft.mode, draft=dict(draft.values)
            )
        return OnboardingResult(
            "ready_to_confirm", draft.step, draft.mode, draft=dict(draft.values)
        )

    parsed = _apply_answer(draft.step, normalized)
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


async def confirm_preferences_edit(
    store: IdentityStore,
    *,
    owner_id: UUID,
    now: datetime,
) -> OnboardingResult:
    _require_aware(now)
    await store.lock_processing_consent(owner_id)
    draft = await store.get_onboarding(owner_id)
    if draft is None or draft.mode != "preferences":
        return OnboardingResult("not_in_progress")
    if draft.step == OnboardingStep.COMPLETE:
        return OnboardingResult(
            "already_complete", preferences=await store.get_preferences(owner_id)
        )
    if draft.step != OnboardingStep.CONFIRM:
        return OnboardingResult("confirmation_not_ready", draft.step, draft.mode)
    current = await store.get_preferences(owner_id)
    if current is None or current.version != draft.base_profile_version:
        await store.cancel_onboarding(owner_id)
        return OnboardingResult(
            "version_conflict", draft.step, draft.mode, draft=dict(draft.values)
        )
    preferences = _preferences_from_draft(draft.values, current.version + 1)
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

    previous_zone = current.home_zone
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
        if previous_zone != saved.home_zone
        else None
    )
    return OnboardingResult("confirmed", preferences=saved, home_zone_change=handoff)


def _apply_answer(step: str, text: str) -> dict[str, object] | None:
    if text.casefold() in {"keep", "same", "оставить"}:
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
        zone = _resolve_home_zone(text)
        return {"home_zone": zone} if zone else None
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


def _resolve_home_zone(text: str) -> str | None:
    try:
        return validate_home_zone(text)
    except ValueError:
        pass
    city = "_".join(text.casefold().split())
    aliases = {
        "алматы": "Asia/Almaty",
        "астана": "Asia/Almaty",
        "astana": "Asia/Almaty",
        "лондон": "Europe/London",
        "москва": "Europe/Moscow",
        "нью-йорк": "America/New_York",
        "париж": "Europe/Paris",
    }
    if city in aliases:
        return aliases[city]
    matches = [
        zone
        for zone in available_timezones()
        if "/" in zone and zone.rsplit("/", 1)[-1].casefold() == city
    ]
    return matches[0] if len(matches) == 1 else None


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("preference timestamps must be timezone-aware")
