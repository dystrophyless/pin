"""Route Telegram start and preferences commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import (
    OnboardingReply,
    render_onboarding,
    render_start,
)
from pin.adapters.telegram.presentation.templates import processing_consent_required_text
from pin.contexts.identity.application.contracts import IdentityStore
from pin.contexts.identity.application.onboarding import (
    OnboardingResult,
    answer_onboarding,
    begin_preferences_edit,
    confirm_preferences_edit,
)
from pin.contexts.identity.application.processing_consent import get_processing_consent


class DispatchDisposition(StrEnum):
    HANDLED = "handled"
    UNHANDLED = "unhandled"


@dataclass(frozen=True, slots=True)
class PrivateMessageResult:
    disposition: DispatchDisposition
    onboarding: OnboardingResult
    reply: OnboardingReply | None


async def handle_private_text(
    store: IdentityStore,
    *,
    owner_id: UUID,
    text: str,
    now: datetime,
) -> PrivateMessageResult:
    """Handle start and optional settings; ordinary plans stay unhandled."""
    command = _command(text)
    if command == "/start":
        profile = await store.get_preferences(owner_id)
        if profile is not None:
            consent = await get_processing_consent(store, owner_id=owner_id)
            return PrivateMessageResult(
                DispatchDisposition.HANDLED,
                OnboardingResult("started_bot", preferences=profile),
                render_start(profile, ai_enabled=consent.state == "granted"),
            )
        result = OnboardingResult("not_in_progress")
    elif command == "/settings":
        result = await begin_preferences_edit(store, owner_id=owner_id)
    else:
        result = await answer_onboarding(store, owner_id=owner_id, text=text)
        if result.state == "not_in_progress":
            profile = await store.get_preferences(owner_id)
            result = OnboardingResult("unhandled", preferences=profile)
            if profile is not None and command is None:
                consent = await get_processing_consent(store, owner_id=owner_id)
                if consent.state != "granted":
                    notice = processing_consent_required_text(profile.language)
                    return PrivateMessageResult(
                        DispatchDisposition.HANDLED,
                        result,
                        OnboardingReply(escape_markdown_v2(notice), notice),
                    )
        elif result.state == "ready_to_confirm":
            result = await confirm_preferences_edit(store, owner_id=owner_id, now=now)
    disposition = (
        DispatchDisposition.UNHANDLED
        if result.state == "unhandled"
        else DispatchDisposition.HANDLED
    )
    return PrivateMessageResult(disposition, result, render_onboarding(result))


def _command(text: str) -> str | None:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if first.startswith("/"):
        return first.split("@", maxsplit=1)[0].casefold()
    return None
