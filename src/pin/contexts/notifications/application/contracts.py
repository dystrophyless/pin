"""Application ports and immutable Notification schedule contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pin.contexts.notifications.domain.reminders import OmittedReminder, ReminderCandidate


@dataclass(frozen=True, slots=True)
class ScheduleChange:
    event_id: UUID
    event_type: str
    aggregate_id: UUID
    aggregate_version: int
    owner_id: UUID

    def __post_init__(self) -> None:
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        if self.event_type not in SCHEDULE_EVENT_TYPES:
            raise ValueError("event_type is not a supported Planning schedule event")


@dataclass(frozen=True, slots=True)
class CurrentOccurrenceSchedule:
    occurrence_id: UUID
    occurrence_version: int
    state: str
    approved_preferences_version: int | None
    event_start_at: datetime | None
    reminders: tuple[ReminderCandidate, ...]
    omitted: tuple[OmittedReminder, ...] = ()
    series_id: UUID | None = None
    series_version: int | None = None
    series_state: str | None = None
    source_created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.occurrence_version < 1:
            raise ValueError("occurrence_version must be positive")
        if self.state not in {"open", "completed", "cancelled"}:
            raise ValueError("unsupported occurrence state")
        if self.approved_preferences_version is not None and self.approved_preferences_version < 1:
            raise ValueError("approved_preferences_version must be positive when present")
        if self.series_id is None and self.series_version is not None:
            raise ValueError("series_version requires series_id")
        if self.series_id is not None and (self.series_version is None or self.series_version < 1):
            raise ValueError("a series occurrence requires a positive series_version")
        if self.series_id is None and self.series_state is not None:
            raise ValueError("series_state requires series_id")
        if self.series_state is not None and self.series_state not in {"active", "stopped"}:
            raise ValueError("series_state must be active or stopped")
        if self.event_start_at is not None and (
            self.event_start_at.tzinfo is None or self.event_start_at.utcoffset() is None
        ):
            raise ValueError("event_start_at must be timezone-aware")
        if len({(r.offset_kind, r.offset_amount) for r in self.reminders}) != len(self.reminders):
            raise ValueError("schedule snapshot reminder offsets must be unique")


@dataclass(frozen=True, slots=True)
class CurrentPlanSchedule:
    owner_id: UUID
    plan_item_id: UUID
    plan_version: int
    preferences_version: int
    occurrences: tuple[CurrentOccurrenceSchedule, ...]

    def __post_init__(self) -> None:
        if self.plan_version < 1 or self.preferences_version < 1:
            raise ValueError("plan and preferences versions must be positive")
        if len({occurrence.occurrence_id for occurrence in self.occurrences}) != len(
            self.occurrences
        ):
            raise ValueError("schedule snapshot occurrence IDs must be unique")


class CurrentScheduleStore(Protocol):
    """Planning-owned, caller-UoW reader that locks current rows before writes."""

    async def lock_current_schedule(self, change: ScheduleChange) -> CurrentPlanSchedule | None: ...


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    created: int = 0
    updated: int = 0
    invalidated: int = 0


@dataclass(frozen=True, slots=True)
class AccessResumeReminder:
    offset_kind: str
    offset_amount: int
    source: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class AccessResumeOccurrence:
    owner_id: UUID
    occurrence_id: UUID
    occurrence_version: int
    state: str
    event_start_at: datetime | None
    reminders: tuple[AccessResumeReminder, ...]
    plan_item_id: UUID
    plan_version: int
    preferences_version: int
    series_id: UUID | None = None
    series_version: int | None = None


@dataclass(frozen=True, slots=True)
class ResumeAfterAccess:
    owner_id: UUID
    period_id: str
    at: datetime
    occurrences: tuple[AccessResumeOccurrence, ...]


@dataclass(frozen=True, slots=True)
class ResumeAfterAccessResult:
    rearmed: int = 0
    skipped_stale: int = 0


class ReminderIntentStore(Protocol):
    """Notifications-owned intent persistence using the caller's transaction."""

    async def reconcile_snapshot(
        self,
        change: ScheduleChange,
        current: CurrentPlanSchedule,
        *,
        at: datetime,
    ) -> ReconciliationResult: ...

    async def record_stale_change(
        self, change: ScheduleChange, *, at: datetime
    ) -> ReconciliationResult: ...


class AccessResumeStore(Protocol):
    async def resume_after_access(self, command: ResumeAfterAccess) -> ResumeAfterAccessResult: ...


SCHEDULE_EVENT_TYPES = frozenset(
    {
        "planning.plan_created",
        "planning.plan_rescheduled",
        "planning.occurrence_completed",
        "planning.occurrence_cancelled",
        "planning.series_created",
        "planning.series_occurrence_created",
        "planning.series_generated",
        "planning.series_occurrence_rescheduled",
        "planning.series_exception_applied",
        "planning.series_stopped",
        "planning.series_approval_updated",
    }
)
