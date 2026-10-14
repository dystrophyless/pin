"""Entitlement and period-scoped usage rules for the billing context."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum


class PeriodKind(StrEnum):
    TRIAL = "trial"
    PAID = "paid"


class AccountLifecycle(StrEnum):
    MISSING = "missing"
    PRESENT = "present"
    DELETED = "deleted"


class Operation(StrEnum):
    CREATE_PLAN = "create_plan"
    RESCHEDULE_PLAN = "reschedule_plan"
    AI_TURN = "ai_turn"
    VOICE_TRANSCRIPTION = "voice_transcription"
    VIEW_PLAN = "view_plan"
    COMPLETE_PLAN = "complete_plan"
    CANCEL_PLAN = "cancel_plan"
    REMINDER_DELIVERY = "reminder_delivery"


class AccessNoticeKind(StrEnum):
    EXPIRY_24H = "expiry_24h"
    EXPIRY_1H = "expiry_1h"


class AccessNoticeStatus(StrEnum):
    SEND = "send"
    DEFER = "defer"
    SKIP = "skip"


class AccessDenial(StrEnum):
    ACCOUNT_DELETED = "account_deleted"
    ACCESS_EXPIRED = "access_expired"


class ReservationState(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class AccessError(ValueError):
    """Base class for invalid access or usage transitions."""


class QuotaExceeded(AccessError):
    """A reservation would exceed the period's configured allowance."""


class IdempotencyConflict(AccessError):
    """An idempotency key was replayed with different data or outcome."""


class InvalidTransition(AccessError):
    """A reservation is already terminal or has passed its expiry."""


@dataclass(frozen=True, slots=True)
class Allowance:
    ai_turns: int
    voice_seconds: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0 for value in (self.ai_turns, self.voice_seconds)
        ):
            raise ValueError("allowance limits must be positive integers")


@dataclass(frozen=True, slots=True)
class PlanPolicy:
    kind: PeriodKind
    duration: timedelta
    allowance: Allowance
    version: str = "v1"

    def __post_init__(self) -> None:
        if self.duration <= timedelta(0):
            raise ValueError("access period duration must be positive")
        if not self.version.strip() or len(self.version) > 64:
            raise ValueError("policy version must contain 1 to 64 characters")

    @classmethod
    def trial(cls) -> PlanPolicy:
        return cls(PeriodKind.TRIAL, timedelta(days=7), Allowance(250, 15 * 60))

    @classmethod
    def paid(cls) -> PlanPolicy:
        return cls(PeriodKind.PAID, timedelta(days=30), Allowance(1000, 60 * 60))


@dataclass(frozen=True, slots=True)
class AccessPeriod:
    period_id: str
    policy: PlanPolicy
    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if not self.period_id.strip() or len(self.period_id) > 128:
            raise ValueError("period_id must contain 1 to 128 characters")
        _require_aware(self.starts_at, "starts_at")
        _require_aware(self.ends_at, "ends_at")
        if self.ends_at <= self.starts_at:
            raise ValueError("access period must end after it starts")
        if self.ends_at - self.starts_at != self.policy.duration:
            raise ValueError("access period length must match its versioned policy")

    def is_active(self, at: datetime) -> bool:
        _require_aware(at, "at")
        return self.starts_at <= at < self.ends_at


@dataclass(frozen=True, slots=True)
class AccessAccount:
    periods: tuple[AccessPeriod, ...] = ()
    deleted: bool = False

    def __post_init__(self) -> None:
        period_ids = [period.period_id for period in self.periods]
        if len(period_ids) != len(set(period_ids)):
            raise ValueError("period identifiers must be unique within an account")

    def active_period(self, at: datetime) -> AccessPeriod | None:
        active = [period for period in self.periods if period.is_active(at)]
        if not active:
            return None
        # A confirmed paid period supersedes a still-open trial allocation.
        return max(
            active,
            key=lambda period: (period.policy.kind is PeriodKind.PAID, period.starts_at),
        )


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    operation: Operation
    denial: AccessDenial | None
    period_id: str | None
    period_ends_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccessNoticeAuthorization:
    status: AccessNoticeStatus
    kind: AccessNoticeKind
    period: AccessPeriod
    at: datetime
    defer_until: datetime | None = None
    remaining_seconds: int = 0


def authorize_access_notice(
    period: AccessPeriod,
    kind: AccessNoticeKind,
    *,
    at: datetime,
    quiet_until: datetime | None = None,
) -> AccessNoticeAuthorization:
    """Authorize a queued expiry warning against live coverage and quiet hours."""
    _require_aware(at, "at")
    if quiet_until is not None:
        _require_aware(quiet_until, "quiet_until")

    if at < period.starts_at:
        return AccessNoticeAuthorization(
            AccessNoticeStatus.DEFER, kind, period, at, period.starts_at
        )
    if at >= period.ends_at:
        return AccessNoticeAuthorization(AccessNoticeStatus.SKIP, kind, period, at)

    until_due = period.ends_at - timedelta(hours=24 if kind is AccessNoticeKind.EXPIRY_24H else 1)
    if at < until_due:
        return AccessNoticeAuthorization(AccessNoticeStatus.DEFER, kind, period, at, until_due)

    remaining = period.ends_at - at
    if kind is AccessNoticeKind.EXPIRY_24H and remaining <= timedelta(hours=1):
        return AccessNoticeAuthorization(AccessNoticeStatus.SKIP, kind, period, at)

    if quiet_until is not None and quiet_until > at:
        if quiet_until >= period.ends_at:
            return AccessNoticeAuthorization(AccessNoticeStatus.SKIP, kind, period, at)
        return AccessNoticeAuthorization(
            AccessNoticeStatus.DEFER, kind, period, at, quiet_until
        )

    remaining_seconds = max(1, int(remaining.total_seconds()))
    return AccessNoticeAuthorization(
        AccessNoticeStatus.SEND,
        kind,
        period,
        at,
        remaining_seconds=remaining_seconds,
    )


_ACTIVE_ACCESS = frozenset(
    {
        Operation.CREATE_PLAN,
        Operation.RESCHEDULE_PLAN,
        Operation.AI_TURN,
        Operation.VOICE_TRANSCRIPTION,
        Operation.REMINDER_DELIVERY,
    }
)


def check_access(account: AccessAccount, operation: Operation, at: datetime) -> AccessDecision:
    """Check entitlement independently of the operation's usage allowance."""
    _require_aware(at, "at")
    if account.deleted:
        return AccessDecision(False, operation, AccessDenial.ACCOUNT_DELETED, None)

    active_period = account.active_period(at)
    if operation in _ACTIVE_ACCESS and active_period is None:
        return AccessDecision(False, operation, AccessDenial.ACCESS_EXPIRED, None)
    return AccessDecision(
        True,
        operation,
        None,
        active_period.period_id if active_period else None,
        active_period.ends_at if active_period else None,
    )


@dataclass(frozen=True, slots=True)
class UsageReservation:
    request_id: str
    period_id: str
    ai_turns: int
    voice_seconds: int
    created_at: datetime
    expires_at: datetime
    state: ReservationState = ReservationState.RESERVED
    settled_ai_turns: int = 0
    settled_voice_seconds: int = 0


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    period_id: str
    consumed_ai_turns: int
    reserved_ai_turns: int
    remaining_ai_turns: int
    consumed_voice_seconds: int
    reserved_voice_seconds: int
    remaining_voice_seconds: int


@dataclass(slots=True)
class UsageLedger:
    """Single-period ledger; its repository must serialize reserve/finalize transactions."""

    period_id: str
    allowance: Allowance
    period_ends_at: datetime
    _reservations: dict[str, UsageReservation] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.period_id.strip():
            raise ValueError("period_id must not be empty")
        _require_aware(self.period_ends_at, "period_ends_at")

    @classmethod
    def restore(
        cls,
        *,
        period_id: str,
        allowance: Allowance,
        period_ends_at: datetime,
        reservations: tuple[UsageReservation, ...],
    ) -> UsageLedger:
        """Rehydrate the period ledger from its durable reservation records."""
        ledger = cls(period_id, allowance, period_ends_at)
        for reservation in reservations:
            if reservation.period_id != period_id:
                raise InvalidTransition("reservation belongs to another usage period")
            _require_aware(reservation.created_at, "reservation.created_at")
            _require_aware(reservation.expires_at, "reservation.expires_at")
            _require_units(reservation.ai_turns, reservation.voice_seconds, allow_zero=False)
            _require_units(
                reservation.settled_ai_turns,
                reservation.settled_voice_seconds,
                allow_zero=True,
            )
            if not reservation.request_id.strip() or len(reservation.request_id) > 128:
                raise ValueError("restored request_id must contain 1 to 128 characters")
            if reservation.request_id in ledger._reservations:
                raise IdempotencyConflict("duplicate request id in restored usage period")
            if reservation.ai_turns > 1:
                raise InvalidTransition("one reservation can consume at most one AI turn")
            if reservation.settled_ai_turns > reservation.ai_turns or (
                reservation.settled_voice_seconds > reservation.voice_seconds
            ):
                raise InvalidTransition("restored settlement exceeds its reservation")
            if reservation.state is ReservationState.SETTLED:
                if reservation.ai_turns and reservation.settled_ai_turns != 1:
                    raise InvalidTransition("a settled AI turn must record its consumed turn")
                if reservation.voice_seconds and reservation.settled_voice_seconds == 0:
                    raise InvalidTransition("a settled voice request must record decoded seconds")
            elif reservation.settled_ai_turns or reservation.settled_voice_seconds:
                raise InvalidTransition("only settled reservations may contain consumed units")
            ledger._reservations[reservation.request_id] = reservation
        snapshot = ledger.snapshot()
        if snapshot.consumed_ai_turns + snapshot.reserved_ai_turns > allowance.ai_turns or (
            snapshot.consumed_voice_seconds + snapshot.reserved_voice_seconds
            > allowance.voice_seconds
        ):
            raise QuotaExceeded("stored usage exceeds the period allowance")
        return ledger

    def reserve(
        self,
        *,
        request_id: str,
        period_id: str,
        ai_turns: int,
        voice_seconds: int,
        at: datetime,
        expires_at: datetime,
    ) -> UsageReservation:
        _require_aware(at, "at")
        _require_aware(expires_at, "expires_at")
        if period_id != self.period_id:
            raise InvalidTransition("reservation period does not match this ledger")
        if not request_id.strip() or len(request_id) > 128:
            raise ValueError("request_id must contain 1 to 128 characters")
        _require_units(ai_turns, voice_seconds)
        if ai_turns > 1:
            raise ValueError("one request can reserve at most one AI turn")

        previous = self._reservations.get(request_id)
        if previous is not None:
            same_request = (
                previous.period_id == period_id
                and previous.ai_turns == ai_turns
                and previous.voice_seconds == voice_seconds
            )
            if not same_request:
                raise IdempotencyConflict("request_id was reused with different reservation data")
            return previous

        if expires_at <= at:
            raise ValueError("reservation expiry must be after creation")
        if at >= self.period_ends_at or expires_at > self.period_ends_at:
            raise InvalidTransition("reservation must start and expire within its access period")

        snapshot = self.snapshot()
        if (
            ai_turns > snapshot.remaining_ai_turns
            or voice_seconds > snapshot.remaining_voice_seconds
        ):
            raise QuotaExceeded("requested usage exceeds the active period allowance")

        reservation = UsageReservation(
            request_id=request_id,
            period_id=period_id,
            ai_turns=ai_turns,
            voice_seconds=voice_seconds,
            created_at=at,
            expires_at=expires_at,
        )
        self._reservations[request_id] = reservation
        return reservation

    def settle(
        self,
        *,
        request_id: str,
        ai_turns: int,
        voice_seconds: int,
        at: datetime,
    ) -> UsageReservation:
        _require_aware(at, "at")
        _require_units(ai_turns, voice_seconds, allow_zero=True)
        reservation = self._get_reservation(request_id)
        if reservation.state is ReservationState.SETTLED:
            if (reservation.settled_ai_turns, reservation.settled_voice_seconds) != (
                ai_turns,
                voice_seconds,
            ):
                raise IdempotencyConflict("settlement replay changed the measured usage")
            return reservation
        self._require_open(reservation, at)
        if ai_turns > reservation.ai_turns or voice_seconds > reservation.voice_seconds:
            raise InvalidTransition("settled usage cannot exceed its reservation")
        if reservation.ai_turns and ai_turns != 1:
            raise InvalidTransition("successful AI work must settle its reserved user turn")
        if reservation.voice_seconds and voice_seconds == 0:
            raise InvalidTransition("successful voice work must settle decoded seconds")
        settled = replace(
            reservation,
            state=ReservationState.SETTLED,
            settled_ai_turns=ai_turns,
            settled_voice_seconds=voice_seconds,
        )
        self._reservations[request_id] = settled
        return settled

    def resize_voice(self, request_id: str, voice_seconds: int, at: datetime) -> UsageReservation:
        """Replace an open reservation's voice hold without changing its AI hold."""
        _require_aware(at, "at")
        if type(voice_seconds) is not int or voice_seconds <= 0:
            raise ValueError("voice_seconds must be a positive integer")
        reservation = self._get_reservation(request_id)
        if reservation.voice_seconds <= 0:
            raise InvalidTransition("reservation has no voice hold to resize")
        self._require_open(reservation, at)
        if reservation.voice_seconds == voice_seconds:
            return reservation

        snapshot = self.snapshot()
        remaining_without_self = (
            self.allowance.voice_seconds
            - snapshot.consumed_voice_seconds
            - snapshot.reserved_voice_seconds
            + reservation.voice_seconds
        )
        if voice_seconds > remaining_without_self:
            raise QuotaExceeded("resized voice hold exceeds the period allowance")

        resized = replace(reservation, voice_seconds=voice_seconds)
        self._reservations[request_id] = resized
        return resized

    def refund(self, *, request_id: str, at: datetime) -> UsageReservation:
        _require_aware(at, "at")
        reservation = self._get_reservation(request_id)
        if reservation.state is ReservationState.REFUNDED:
            return reservation
        self._require_open(reservation, at)
        refunded = replace(reservation, state=ReservationState.REFUNDED)
        self._reservations[request_id] = refunded
        return refunded

    def expire(self, *, at: datetime) -> tuple[UsageReservation, ...]:
        """Release reservations at caller-supplied deadlines; late work cannot be charged."""
        _require_aware(at, "at")
        expired: list[UsageReservation] = []
        for request_id, reservation in tuple(self._reservations.items()):
            if reservation.state is ReservationState.RESERVED and reservation.expires_at <= at:
                updated = replace(reservation, state=ReservationState.EXPIRED)
                self._reservations[request_id] = updated
                expired.append(updated)
        return tuple(expired)

    def reservation(self, request_id: str) -> UsageReservation | None:
        return self._reservations.get(request_id)

    def snapshot(self) -> UsageSnapshot:
        consumed_ai = sum(item.settled_ai_turns for item in self._reservations.values())
        consumed_voice = sum(item.settled_voice_seconds for item in self._reservations.values())
        reserved_ai = sum(
            item.ai_turns
            for item in self._reservations.values()
            if item.state is ReservationState.RESERVED
        )
        reserved_voice = sum(
            item.voice_seconds
            for item in self._reservations.values()
            if item.state is ReservationState.RESERVED
        )
        return UsageSnapshot(
            period_id=self.period_id,
            consumed_ai_turns=consumed_ai,
            reserved_ai_turns=reserved_ai,
            remaining_ai_turns=max(0, self.allowance.ai_turns - consumed_ai - reserved_ai),
            consumed_voice_seconds=consumed_voice,
            reserved_voice_seconds=reserved_voice,
            remaining_voice_seconds=max(
                0, self.allowance.voice_seconds - consumed_voice - reserved_voice
            ),
        )

    def _get_reservation(self, request_id: str) -> UsageReservation:
        try:
            return self._reservations[request_id]
        except KeyError as exc:
            raise InvalidTransition("reservation does not exist in this period") from exc

    def _require_open(self, reservation: UsageReservation, at: datetime) -> None:
        if reservation.state is not ReservationState.RESERVED:
            raise InvalidTransition("reservation already has a terminal outcome")
        if reservation.expires_at <= at or self.period_ends_at <= at:
            raise InvalidTransition("reservation expired before its terminal outcome")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_units(ai_turns: int, voice_seconds: int, *, allow_zero: bool = False) -> None:
    if any(type(value) is not int or value < 0 for value in (ai_turns, voice_seconds)):
        raise ValueError("usage units must be non-negative integers")
    if not allow_zero and ai_turns == voice_seconds == 0:
        raise ValueError("a reservation must consume at least one usage unit")
