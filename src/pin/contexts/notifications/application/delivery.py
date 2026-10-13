"""Application ports for external reminder delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID


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
