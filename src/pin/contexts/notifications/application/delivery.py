"""Application ports for external reminder delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

ACCOUNTABILITY_DELIVERY_JOB_KIND = "accountability.deliver"
DELETED_ACCOUNT_DENIAL_TEXT = "Account data is unavailable. Данные недоступны."


def is_deleted_account_denial(payload: Mapping[str, object]) -> bool:
    """Allow only the fixed, non-personal response generated after a deletion fence."""
    return (
        payload.get("safe_deleted_account_denial") is True
        and payload.get("handling") == "denied"
        and payload.get("markdown") == DELETED_ACCOUNT_DENIAL_TEXT
        and payload.get("plain_text") == DELETED_ACCOUNT_DENIAL_TEXT
        and payload.get("reply_markup") is None
        and payload.get("requires_preparation", False) is False
    )


@dataclass(frozen=True, slots=True)
class RecoverySummaryRequest:
    """One owner-scoped unresolved-work summary per caller-owned recovery episode."""

    owner_id: UUID
    episode_key: str

    def __post_init__(self) -> None:
        if not self.episode_key.strip() or len(self.episode_key) > 180:
            raise ValueError("episode_key must contain 1 to 180 characters")


@dataclass(frozen=True, slots=True)
class ReminderSend:
    destination_id: int
    text: str


@dataclass(frozen=True, slots=True)
class TelegramReplySend:
    destination_id: int
    markdown: str
    plain_text: str
    reply_markup: dict[str, object] | None = None
    parse_mode: Literal["MarkdownV2"] | None = "MarkdownV2"


@dataclass(frozen=True, slots=True)
class ReplyPreparation:
    """A domain-owned dispatch decision; no external attempt has started yet."""

    message: TelegramReplySend | None = None
    deferred_until: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.message is not None and (
            self.deferred_until is not None or self.reason is not None
        ):
            raise ValueError("a prepared reply cannot also defer or skip")
        if self.deferred_until is not None and (
            self.deferred_until.tzinfo is None or self.deferred_until.utcoffset() is None
        ):
            raise ValueError("reply deferral must be timezone-aware")
        if self.message is None and self.deferred_until is None and self.reason is None:
            raise ValueError("a skipped reply requires a reason")
        if self.reason is not None and (not self.reason.strip() or len(self.reason) > 80):
            raise ValueError("reply preparation reason must contain 1 to 80 characters")


class AccountabilityTransport(Protocol):
    async def send_reply(self, message: TelegramReplySend) -> str | None: ...


class KnownDeliveryFailure(Exception):
    """The provider definitively rejected the send; optional retry is safe."""

    def __init__(
        self,
        code: str,
        *,
        retry_after: timedelta | None = None,
        blocked: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.blocked = blocked


class UnknownDeliveryFailure(Exception):
    """The provider outcome cannot be established; never retry automatically."""

    def __init__(self, code: str = "transport_outcome_unknown") -> None:
        super().__init__(code)
        self.code = code
