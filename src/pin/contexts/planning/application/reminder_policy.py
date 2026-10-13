"""Planning adapter for the published Notifications quiet-hour policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time

from pin.contexts.notifications.api import (
    OmittedReminder as NotificationOmission,
)
from pin.contexts.notifications.api import (
    ReminderCandidate,
    project_reminders,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ReminderResolution,
    ReminderSchedule,
    ResolvedSchedule,
)
from pin.contexts.planning.domain.recurrence import RecurrenceProjection


def apply_quiet_policy(
    schedule: ResolvedSchedule,
    resolution: ReminderResolution,
    *,
    now: datetime,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
) -> tuple[ReminderResolution, tuple[ReminderNotice, ...]]:
    """Pass Planning-resolved instants through Notifications and map its DTOs."""
    if schedule.anchor_at is None:
        return resolution, ()
    result = project_reminders(
        tuple(
            ReminderCandidate(
                item.offset.kind.value,
                item.offset.amount,
                item.offset.source,
                item.due_at,
            )
            for item in resolution.scheduled
        ),
        tuple(
            NotificationOmission(
                item.offset.kind.value,
                item.offset.amount,
                item.offset.source,
                item.due_at,
                item.reason,
            )
            for item in resolution.omitted
        ),
        event_start_at=schedule.anchor_at,
        now=now,
        quiet_zone=quiet_zone,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )
    scheduled = tuple(
        ReminderSchedule(
            ReminderOffset(OffsetKind(item.offset_kind), item.offset_amount, item.source),
            item.due_at,
        )
        for item in result.scheduled
    )
    omitted = tuple(
        ReminderOmission(
            ReminderOffset(OffsetKind(item.offset_kind), item.offset_amount, item.source),
            item.due_at,
            item.reason,
        )
        for item in result.omitted
    )
    notices = tuple(
        ReminderNotice(
            item.kind.value,
            ReminderOffset(OffsetKind(item.offset_kind), item.offset_amount, item.source),
            item.original_due_at,
            item.due_at,
        )
        for item in result.notices
    )
    return ReminderResolution(scheduled, omitted), notices


def apply_recurrence_quiet_policy(
    projection: RecurrenceProjection,
    *,
    now: datetime,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
) -> RecurrenceProjection:
    occurrences = []
    for occurrence in projection.occurrences:
        resolution, notices = apply_quiet_policy(
            occurrence.schedule,
            ReminderResolution(
                tuple(ReminderSchedule(offset, due_at) for offset, due_at in occurrence.reminders),
                occurrence.omitted_reminders,
            ),
            now=now,
            quiet_zone=quiet_zone,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
        )
        occurrences.append(
            replace(
                occurrence,
                reminders=tuple((item.offset, item.due_at) for item in resolution.scheduled),
                omitted_reminders=tuple(
                    resolution.omitted
                ),
                reminder_notices=notices,
            )
        )
    return replace(projection, occurrences=tuple(occurrences))
