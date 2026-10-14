"""Commands and repository boundary for access and usage operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pin.contexts.billing.domain.access import (
    AccessDecision,
    AccessNoticeAuthorization,
    AccountLifecycle,
    Operation,
    PeriodKind,
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
class ResizeVoiceUsage:
    owner_id: UUID
    request_id: str
    voice_seconds: int
    at: datetime


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


@dataclass(frozen=True, slots=True)
class ActivatePaidPeriod:
    """Record provider-confirmed paid coverage; receipt validation belongs to the caller."""

    owner_id: UUID
    period_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class CheckAccessNotice:
    owner_id: UUID
    notice_id: UUID
    period_id: str
    at: datetime


class AccessNoticeQuietPolicy(Protocol):
    """Read and lock current quiet preferences after Billing's period locks."""

    async def quiet_until(self, owner_id: UUID, at: datetime) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class SubscriptionStatus:
    owner_id: UUID
    account_lifecycle: AccountLifecycle
    period_id: str | None
    period_kind: PeriodKind | None
    starts_at: datetime | None
    ends_at: datetime | None
    active: bool
    trial_used: bool
    ai_turn_limit: int | None
    ai_turns_remaining: int | None
    voice_seconds_limit: int | None
    voice_seconds_remaining: int | None


@dataclass(frozen=True, slots=True)
class ReadSubscriptionStatus:
    owner_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class RetainBillingIdentity:
    owner_id: UUID
    telegram_user_id: int
    at: datetime


@dataclass(frozen=True, slots=True)
class RestoreReturningPaidAccess:
    owner_id: UUID
    telegram_user_id: int
    at: datetime


class AccessLifecycleStore(Protocol):
    async def retain_owner_for_return(self, command: RetainBillingIdentity) -> bool: ...

    async def resolve_returning_owner(self, telegram_user_id: int) -> UUID | None: ...

    async def restore_returning_paid_access(self, command: RestoreReturningPaidAccess) -> bool: ...

    async def activate_paid_period(self, command: ActivatePaidPeriod) -> bool: ...

    async def enqueue_due_access_notices(self, *, at: datetime, limit: int = 100) -> int: ...

    async def authorize_access_notice(
        self,
        command: CheckAccessNotice,
        *,
        quiet_policy: AccessNoticeQuietPolicy,
    ) -> AccessNoticeAuthorization: ...

    async def read_subscription_status(
        self, query: ReadSubscriptionStatus
    ) -> SubscriptionStatus: ...


class AccessUsageStore(AccessLifecycleStore, Protocol):
    """Atomic owner/access/quota boundary implemented by billing infrastructure."""

    async def check_access(self, query: CheckAccess) -> AccessDecision:
        """Return current entitlement rights for one owner and operation."""

    async def lock_access(self, query: CheckAccess) -> AccessDecision:
        """Lock current account/period facts until caller UoW ends; evaluate supplied clock."""

    async def account_lifecycle(self, owner_id: UUID) -> AccountLifecycle:
        """Return whether the owner account is missing, present, or tombstoned."""

    async def lock_account_lifecycle(self, owner_id: UUID) -> AccountLifecycle:
        """Lock account lifecycle through caller authorization; missing stays missing."""

    async def reserve_usage(self, command: ReserveUsage) -> ReserveUsageResult:
        """Check current entitlement and reserve all requested dimensions atomically."""

    async def resize_voice_usage(self, command: ResizeVoiceUsage) -> UsageReservation:
        """Resize an open voice hold against its original period in the caller's UoW."""

    async def get_usage_reservation(
        self, owner_id: UUID, request_id: str
    ) -> UsageReservation | None:
        """Read an owner's reservation without creating or changing usage facts."""

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
