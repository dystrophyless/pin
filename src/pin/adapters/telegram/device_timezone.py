"""Bind a Telegram timezone widget to its current profile or settings step."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.home_zone_runtime import preview_home_zone_edit
from pin.adapters.telegram.onboarding import DispatchDisposition, PrivateMessageResult
from pin.adapters.telegram.presentation.callbacks import CallbackTokens, InvalidCallbackToken
from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import (
    OnboardingReply,
    render_device_timezone,
    render_onboarding,
)
from pin.adapters.telegram.presentation.timezone_widget import render_timezone_widget
from pin.adapters.telegram.presentation.tokens import SqlAlchemyCallbackTokenStore
from pin.contexts.identity.api import (
    IdentityStore,
    OnboardingDraft,
    OnboardingResult,
    PreferencesSnapshot,
    answer_onboarding,
    resolve_home_zone,
)
from pin.contexts.identity.domain.preferences import validate_home_zone

_PROFILE_ACTION = "device_zone.profile"
_DRAFT_ACTION = "device_zone.draft"


async def handle_timezone_command(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    profile: PreferencesSnapshot,
    zone_input: str | None,
    now: datetime,
) -> PrivateMessageResult:
    if _active_edit(await identity.get_onboarding(profile.owner_id)):
        text = (
            "Завершите текущее изменение настроек или отправьте /cancel, затем /timezone."
            if profile.language == "ru"
            else "Finish the current edit or send /cancel, then /timezone."
        )
        return _timezone_reply(profile, text)
    if zone_input is None:
        return PrivateMessageResult(
            DispatchDisposition.HANDLED,
            OnboardingResult("device_timezone", preferences=profile),
            render_device_timezone(profile),
        )
    zone = resolve_home_zone(zone_input)
    if zone is None:
        text = (
            "Не удалось определить пояс. Попробуйте /timezone Алматы или /timezone Asia/Almaty."
            if profile.language == "ru"
            else "Could not identify that zone. Try /timezone London or /timezone Europe/London."
        )
        return _timezone_reply(profile, text)
    return await _preview_timezone(session, identity, profile, zone, now)


async def issue_timezone_widget(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    owner_id: UUID,
    result: OnboardingResult,
    first_start: bool,
    now: datetime,
) -> str | None:
    if result.step == "home_zone" and result.mode == "preferences":
        draft = await identity.get_onboarding(owner_id)
        if draft is None or draft.mode != "preferences" or draft.step != "home_zone":
            return None
        action = _DRAFT_ACTION
        target_id = str(draft.draft_id)
        target_version = draft.version
        language = "ru" if draft.values.get("language") == "ru" else "en"
    elif result.preferences and (
        result.state == "device_timezone" or (first_start and result.state == "started_bot")
    ):
        action = _PROFILE_ACTION
        target_id = "home_zone"
        target_version = result.preferences.version
        language = result.preferences.language
    else:
        return None
    token = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).issue(
        owner_id=owner_id,
        action=action,
        target_id=target_id,
        target_version=target_version,
        expires_at=now + timedelta(minutes=15),
        now=now,
    )
    return render_timezone_widget(language, token)


async def handle_timezone_widget(
    session: AsyncSession,
    identity: IdentityStore,
    *,
    owner_id: UUID,
    profile: PreferencesSnapshot | None,
    data: object,
    now: datetime,
) -> PrivateMessageResult:
    language = profile.language if profile else "en"
    draft = await identity.get_onboarding(owner_id)
    editing = _active_edit(draft)
    try:
        if profile is None or not isinstance(data, str) or len(data.encode("utf-8")) > 64:
            raise InvalidCallbackToken("invalid widget response")
        token, separator, zone = data.partition("|")
        if not separator or (zone != "UTC" and "/" not in zone):
            raise InvalidCallbackToken("missing named time zone")
        zone = validate_home_zone(zone)
        versions = {_PROFILE_ACTION: profile.version}
        if draft is not None and editing and draft.step == "home_zone":
            versions[_DRAFT_ACTION] = draft.version
        binding = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).consume(
            token,
            actor_id=owner_id,
            current_target_versions=versions,
            now=now,
        )
        if binding.action == _DRAFT_ACTION:
            if (
                draft is None
                or binding.target_id != str(draft.draft_id)
                or draft.base_profile_version != profile.version
            ):
                raise InvalidCallbackToken("settings draft changed")
            result = await answer_onboarding(identity, owner_id=owner_id, text=zone)
            return PrivateMessageResult(
                DispatchDisposition.HANDLED, result, render_onboarding(result)
            )
        if binding.target_id != "home_zone" or editing:
            raise InvalidCallbackToken("another settings edit is active")
    except ValueError:
        text = (
            "Не удалось принять пояс. Откройте заново /timezone или продолжите в /settings."
            if language == "ru"
            else "Could not accept the time zone. Reopen /timezone or continue in /settings."
        )
    else:
        return await _preview_timezone(session, identity, profile, zone, now)
    return _timezone_reply(profile, text)


def _active_edit(draft: OnboardingDraft | None) -> bool:
    return draft is not None and draft.mode == "preferences" and draft.step != "complete"


async def _preview_timezone(
    session: AsyncSession,
    identity: IdentityStore,
    profile: PreferencesSnapshot,
    zone: str,
    now: datetime,
) -> PrivateMessageResult:
    if zone != profile.home_zone:
        outcome = await preview_home_zone_edit(
            session, identity, profile=profile, zone=zone, now=now
        )
        return PrivateMessageResult(DispatchDisposition.HANDLED, outcome.onboarding, outcome.reply)
    text = (
        f"Текущий часовой пояс уже {zone}."
        if profile.language == "ru"
        else f"Your time zone is already {zone}."
    )
    return _timezone_reply(profile, text)


def _timezone_reply(
    profile: PreferencesSnapshot | None, text: str
) -> PrivateMessageResult:
    return PrivateMessageResult(
        DispatchDisposition.HANDLED,
        OnboardingResult("device_zone_reply", preferences=profile),
        OnboardingReply(escape_markdown_v2(text), text),
    )
