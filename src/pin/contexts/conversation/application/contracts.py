"""Ports used by owner-filtered conversation interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pin.contexts.conversation.application.dto import ConversationContext, ModelOutcome
from pin.platform.jobs import JobClaim


@dataclass(frozen=True, slots=True)
class CallUsage:
    provider_request_id: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int


@dataclass(frozen=True, slots=True)
class CallStart:
    owner_id: UUID
    turn_id: str
    call_id: UUID
    call_number: int
    model: str
    created_at: datetime


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    REPLAY = "replay"
    DENIED = "denied"


class TurnAllowance(Protocol):
    """Authoritative one-user-turn reservation, implemented by the access owner."""

    async def reserve(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: JobClaim | None = None
    ) -> ReservationStatus:
        """Atomically reserve one AI turn for this user request."""

    async def settle(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: JobClaim | None = None
    ) -> None:
        """Idempotently settle one reservation for the owner/request key."""

    async def refund(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: JobClaim | None = None
    ) -> None:
        """Refund failed interpretation without changing provider-cost records."""


class ProviderUsageRecorder(Protocol):
    async def start_call(self, call: CallStart, *, claim: JobClaim | None = None) -> None:
        """Commit a call reservation before network I/O."""

    async def record_usage(
        self,
        call: CallStart,
        usage: CallUsage,
        *,
        result_category: str,
        claim: JobClaim | None = None,
    ) -> None:
        """Record one provider receipt idempotently, including invalid model output."""

    async def mark_unknown(
        self,
        call: CallStart,
        *,
        result_category: str,
        provider_request_id: str | None = None,
        claim: JobClaim | None = None,
    ) -> None:
        """Keep an unresolved provider outcome explicit; never substitute zero usage."""

    async def store_result(
        self,
        *,
        owner_id: UUID,
        turn_id: str,
        result: dict[str, object],
        provider_request_ids: tuple[str, ...],
        claim: JobClaim | None = None,
    ) -> None:
        """Persist a validated result before idempotent turn settlement."""

    async def get_result(
        self, *, owner_id: UUID, turn_id: str
    ) -> tuple[dict[str, object], tuple[str, ...]] | None:
        """Load a previously validated result for crash-safe duplicate delivery."""


class PlanningAssistant(Protocol):
    async def interpret(
        self, context: ConversationContext, *, at: datetime, claim: JobClaim | None = None
    ) -> ModelOutcome:
        """Return a typed proposal/reply or explicit failure; never mutate business state."""


class CompletionTransport(Protocol):
    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        """Send one bounded, non-streaming completion request."""
