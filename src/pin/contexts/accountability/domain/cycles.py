"""Pure policy for completion checks and the one permitted extra nudge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo


class CycleKind(StrEnum):
    APPOINTMENT = "appointment"
    DEADLINE = "deadline"
    DAILY_REVIEW = "daily_review"


class CycleState(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXHAUSTED = "exhausted"


class MessageSlot(StrEnum):
    INITIAL = "initial"
    NUDGE = "nudge"


class SendOutcome(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


NUDGE_DELAY = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class CheckInCycle:
    id: UUID
    owner_id: UUID
    occurrence_id: UUID
    occurrence_version: int
    kind: CycleKind
    expected_end_at: datetime
    state: CycleState = CycleState.OPEN
    initial_intent_id: UUID | None = None
    initial_confirmed_at: datetime | None = None
    nudge_due_at: datetime | None = None
    nudge_intent_id: UUID | None = None
    nudge_slot_used: bool = False

    def __post_init__(self) -> None:
        if self.owner_id.int == 0 or self.occurrence_id.int == 0:
            raise ValueError("cycle owner and occurrence IDs are required")
        if self.occurrence_version < 1:
            raise ValueError("occurrence_version must be positive")
        _aware(self.expected_end_at, "expected_end_at")
        if self.kind is CycleKind.DAILY_REVIEW:
            raise ValueError("daily reviews are not timed check-in cycles")
        if (self.initial_confirmed_at is None) != (self.nudge_due_at is None):
            raise ValueError("only a confirmed initial send may start the nudge timer")
        if self.nudge_due_at is not None and self.nudge_due_at != (
            self.initial_confirmed_at + NUDGE_DELAY  # type: ignore[operator]
        ):
            raise ValueError("nudge due time must be one hour after confirmed delivery")
        if self.nudge_slot_used and self.nudge_intent_id is None:
            raise ValueError("a used nudge slot must retain its original intent")

    def confirmed_initial(self, *, intent_id: UUID, confirmed_at: datetime) -> CheckInCycle:
        _aware(confirmed_at, "confirmed_at")
        instant = confirmed_at.astimezone(UTC)
        if self.state is not CycleState.OPEN or self.initial_confirmed_at is not None:
            return self
        return replace(
            self,
            initial_intent_id=intent_id,
            initial_confirmed_at=instant,
            nudge_due_at=instant + NUDGE_DELAY,
        )

    def claim_nudge(self, *, intent_id: UUID) -> CheckInCycle:
        if self.state is not CycleState.OPEN or self.nudge_slot_used:
            return self
        if self.nudge_due_at is None:
            return self
        return replace(self, nudge_intent_id=intent_id, nudge_slot_used=True)


@dataclass(frozen=True, slots=True)
class DailyReview:
    id: UUID
    owner_id: UUID
    review_date: date
    zone_name: str
    occurrence_ids: tuple[UUID, ...]
    due_at: datetime
    state: CycleState = CycleState.OPEN
    initial_intent_id: UUID | None = None
    initial_confirmed_at: datetime | None = None
    nudge_due_at: datetime | None = None
    nudge_intent_id: UUID | None = None
    nudge_slot_used: bool = False

    def __post_init__(self) -> None:
        if self.owner_id.int == 0:
            raise ValueError("review owner is required")
        _aware(self.due_at, "due_at")
        ZoneInfo(self.zone_name)
        if len(set(self.occurrence_ids)) != len(self.occurrence_ids):
            raise ValueError("daily review occurrence IDs must be unique")
        if not self.occurrence_ids:
            raise ValueError("daily review requires at least one occurrence")
        if (self.initial_confirmed_at is None) != (self.nudge_due_at is None):
            raise ValueError("only a confirmed initial send may start the nudge timer")
        if self.nudge_due_at is not None and self.nudge_due_at != (
            self.initial_confirmed_at + NUDGE_DELAY  # type: ignore[operator]
        ):
            raise ValueError("nudge due time must be one hour after confirmed delivery")
        if self.nudge_slot_used and self.nudge_intent_id is None:
            raise ValueError("a used nudge slot must retain its original intent")


def review_due_at(review_date: date, review_time: time, zone_name: str) -> datetime:
    """Resolve the owner's editable local review time to one UTC instant."""
    if review_time.tzinfo is not None:
        raise ValueError("review_time must be a local wall-clock time")
    return datetime.combine(review_date, review_time, ZoneInfo(zone_name)).astimezone(UTC)


def appointment_check_due(
    *, kind: str, end_at: datetime | None, deadline_at: datetime | None
) -> datetime | None:
    """Return only an explicit appointment end or persisted deadline instant."""
    if kind == CycleKind.APPOINTMENT.value:
        return end_at.astimezone(UTC) if end_at is not None else None
    if kind == CycleKind.DEADLINE.value:
        return deadline_at.astimezone(UTC) if deadline_at is not None else None
    return None


def eligible_nudge_at(cycle: CheckInCycle | DailyReview, *, now: datetime) -> datetime | None:
    _aware(now, "now")
    if cycle.state is not CycleState.OPEN or cycle.nudge_slot_used:
        return None
    if cycle.nudge_due_at is None or now.astimezone(UTC) < cycle.nudge_due_at:
        return None
    return cycle.nudge_due_at


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
