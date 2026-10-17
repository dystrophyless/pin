"""Published locked snapshot of the current Planning schedule for consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pin.contexts.planning.domain.plans import ReminderOffset, ResolvedSchedule


@dataclass(frozen=True, slots=True)
class PlanningScheduleChange:
    event_id: UUID
    event_type: str
    aggregate_id: UUID
    aggregate_version: int
    owner_id: UUID


@dataclass(frozen=True, slots=True)
class PersistedReminderSchedule:
    offset: ReminderOffset
    due_at: datetime


@dataclass(frozen=True, slots=True)
class PersistedReminderOmission:
    offset: ReminderOffset
    due_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class PlanningOccurrenceSchedule:
    occurrence_id: UUID
    occurrence_version: int
    state: str
    approved_preferences_version: int | None
    schedule: ResolvedSchedule
    reminders: tuple[PersistedReminderSchedule, ...]
    omitted: tuple[PersistedReminderOmission, ...] = ()
    series_id: UUID | None = None
    series_version: int | None = None
    series_state: str | None = None
    source_created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlanningScheduleSnapshot:
    owner_id: UUID
    plan_item_id: UUID
    plan_version: int
    preferences_version: int
    occurrences: tuple[PlanningOccurrenceSchedule, ...]


@dataclass(frozen=True, slots=True)
class PlanningOwnerOccurrence:
    """One bounded owner-wide current occurrence and its persisted reminders."""

    owner_id: UUID
    plan_item_id: UUID
    plan_version: int
    preferences_version: int
    occurrence: PlanningOccurrenceSchedule


@dataclass(frozen=True, slots=True)
class PlanningOwnerSchedulePage:
    items: tuple[PlanningOwnerOccurrence, ...]
    next_cursor: UUID | None


class PlanningOwnerScheduleReader(Protocol):
    """Bounded, caller-UoW reader for current schedules during access recovery."""

    async def list_current_owner_schedules(
        self, owner_id: UUID, *, after_occurrence_id: UUID | None, limit: int
    ) -> PlanningOwnerSchedulePage: ...


class PlanningScheduleSnapshotStore(Protocol):
    """Caller-UoW reader that locks the current schedule and its version fence."""

    async def lock_schedule(
        self, change: PlanningScheduleChange
    ) -> PlanningScheduleSnapshot | None: ...
