"""Translate private Telegram text updates into Identity onboarding commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pin.adapters.telegram.presentation.onboarding import OnboardingReply, render_onboarding
from pin.contexts.identity.application.contracts import IdentityStore, TrialActivator
from pin.contexts.identity.application.onboarding import (
    OnboardingResult,
    answer_onboarding,
    begin_onboarding,
    begin_preferences_edit,
    confirm_onboarding,
    get_preferences,
)


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
    telegram_user_id: int,
    text: str,
    now: datetime,
    trial_activator: TrialActivator,
) -> PrivateMessageResult:
    """Handle only the owned onboarding/settings flow; ordinary plans stay unhandled."""
    command = _command(text)
    if command == "/start":
        result = await begin_onboarding(store, owner_id=owner_id, now=now)
    elif command == "/settings":
        result = await begin_preferences_edit(store, owner_id=owner_id, now=now)
    else:
        result = await answer_onboarding(store, owner_id=owner_id, text=text)
        if result.state in {"not_in_progress", "already_complete"}:
            profile = await get_preferences(store, owner_id)
            if profile is None:
                result = await begin_onboarding(store, owner_id=owner_id, now=now)
            else:
                result = OnboardingResult("unhandled", preferences=profile)
        elif result.state == "ready_to_confirm":
            result = await confirm_onboarding(
                store,
                trial_activator,
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                now=now,
            )
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
