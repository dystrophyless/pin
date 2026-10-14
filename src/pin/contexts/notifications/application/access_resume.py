"""Resume only persisted reminder intents that remain useful after access returns."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pin.contexts.notifications.application.contracts import (
    AccessResumeOccurrence,
    ResumeAfterAccess,
)


def resumable_access_occurrences(
    owner_id: UUID,
    period_id: str,
    occurrences: tuple[AccessResumeOccurrence, ...],
    *,
    at: datetime,
) -> ResumeAfterAccess:
    """Bound the recovery command to current open events with due persisted reminders."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("resume time must be timezone-aware")
    if not period_id.strip() or len(period_id) > 128:
        raise ValueError("period_id must contain 1 to 128 characters")
    if any(item.owner_id != owner_id for item in occurrences):
        raise ValueError("resume snapshot contains a foreign owner")
    at_utc = at.astimezone(UTC)
    for item in occurrences:
        for value in (item.event_start_at, *(reminder.due_at for reminder in item.reminders)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("schedule times must be timezone-aware")
    selected = tuple(
        occurrence
        for occurrence in occurrences
        if occurrence.state == "open"
        and occurrence.event_start_at is not None
        and occurrence.event_start_at.astimezone(UTC) > at_utc
        and any(reminder.due_at.astimezone(UTC) <= at_utc for reminder in occurrence.reminders)
    )
    return ResumeAfterAccess(owner_id, period_id, at_utc, selected)
