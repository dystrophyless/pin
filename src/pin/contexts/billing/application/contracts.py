"""Commands and repository boundary for access and usage operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pin.contexts.billing.domain.access import (
    AccessDecision,
    Operation,
    UsageReservation,
)


@dataclass(frozen=True, slots=True)
class ReserveUsage:
    owner_id: UUID
    request_id: str
    operation: Operation
    ai_turns: int
    voice_seconds: int
    at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReserveUsageResult:
    access: AccessDecision
    reservation: UsageReservation | None
    created: bool = False


@dataclass(frozen=True, slots=True)
class CheckAccess:
    owner_id: UUID
    operation: Operation
    at: datetime


class TrialActivationStatus(StrEnum):
    ACTIVATED = "activated"
    ALREADY_ACTIVE = "already_active"
    PREVIOUSLY_ACTIVATED = "previously_activated"


@dataclass(frozen=True, slots=True)
class StartTrial:
    owner_id: UUID
    telegram_user_id: int
    at: datetime


@dataclass(frozen=True, slots=True)
class TrialActivationResult:
    status: TrialActivationStatus
    period_id: str | None
    starts_at: datetime | None
    ends_at: datetime | None


class AccessUsageStore(Protocol):
    """Atomic owner/access/quota boundary implemented by billing infrastructure."""

    async def check_access(self, query: CheckAccess) -> AccessDecision:
        """Return current entitlement rights for one owner and operation."""

    async def reserve_usage(self, command: ReserveUsage) -> ReserveUsageResult:
        """Check current entitlement and reserve all requested dimensions atomically."""

    async def settle_usage(
        self,
        *,
        owner_id: UUID,
        request_id: str,
        ai_turns: int,
        voice_seconds: int,
        at: datetime,
    ) -> UsageReservation:
        """Settle once against the reservation's original immutable period."""

    async def refund_usage(
        self, *, owner_id: UUID, request_id: str, at: datetime
    ) -> UsageReservation:
        """Release an unsuccessful operation's reservation exactly once."""

    async def expire_reservations(self, *, at: datetime, limit: int) -> int:
        """Release at most `limit` due reservations and fence any later settlement."""
