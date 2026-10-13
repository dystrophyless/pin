"""Version-fenced reconciliation from committed Planning state."""

from __future__ import annotations

from datetime import datetime

from pin.contexts.notifications.application.contracts import (
    CurrentPlanSchedule,
    CurrentScheduleStore,
    ReconciliationResult,
    ReminderIntentStore,
    ScheduleChange,
)


async def reconcile_schedule_change(
    change: ScheduleChange,
    *,
    current_schedules: CurrentScheduleStore,
    intents: ReminderIntentStore,
    at: datetime,
) -> ReconciliationResult:
    """Read and lock the latest Planning truth before reconciling its intents."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    current = await current_schedules.lock_current_schedule(change)
    if current is None or current.owner_id != change.owner_id:
        return await intents.record_stale_change(change, at=at)
    if not _change_targets_snapshot(change, current):
        return await intents.record_stale_change(change, at=at)
    return await intents.reconcile_snapshot(change, current, at=at)


def _change_targets_snapshot(change: ScheduleChange, current: CurrentPlanSchedule) -> bool:
    if change.event_type in {
        "planning.plan_created",
        "planning.plan_rescheduled",
        "planning.occurrence_completed",
        "planning.occurrence_cancelled",
    }:
        return any(
            occurrence.occurrence_id == change.aggregate_id
            and occurrence.occurrence_version >= change.aggregate_version
            for occurrence in current.occurrences
        )
    if change.event_type in {
        "planning.series_occurrence_created",
        "planning.series_occurrence_rescheduled",
    }:
        return any(
            occurrence.occurrence_id == change.aggregate_id
            and occurrence.occurrence_version >= change.aggregate_version
            for occurrence in current.occurrences
        )
    if change.event_type in {
        "planning.series_created",
        "planning.series_generated",
        "planning.series_exception_applied",
        "planning.series_stopped",
        "planning.series_approval_updated",
    }:
        return any(
            occurrence.series_id == change.aggregate_id
            and occurrence.series_version is not None
            and occurrence.series_version >= change.aggregate_version
            for occurrence in current.occurrences
        )
    return False
