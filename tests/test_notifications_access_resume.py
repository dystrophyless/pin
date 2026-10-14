from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from pin.contexts.notifications.api import (
    AccessResumeOccurrence,
    AccessResumeReminder,
    resumable_access_occurrences,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def occurrence(
    *,
    owner_id: UUID,
    due_at: datetime,
    event_start_at: datetime | None,
    state: str = "open",
) -> AccessResumeOccurrence:
    return AccessResumeOccurrence(
        owner_id=owner_id,
        occurrence_id=uuid4(),
        occurrence_version=1,
        state=state,
        event_start_at=event_start_at,
        reminders=(AccessResumeReminder("elapsed_seconds", 3600, "explicit", due_at),),
        plan_item_id=uuid4(),
        plan_version=1,
        preferences_version=1,
    )


def test_access_resume_keeps_only_due_reminders_for_open_future_events() -> None:
    owner_id = uuid4()
    future_event = occurrence(
        owner_id=owner_id,
        due_at=NOW - timedelta(days=2),
        event_start_at=NOW + timedelta(hours=2),
    )
    not_due = occurrence(
        owner_id=owner_id,
        due_at=NOW + timedelta(minutes=1),
        event_start_at=NOW + timedelta(hours=3),
    )
    event_started = occurrence(
        owner_id=owner_id,
        due_at=NOW - timedelta(days=1),
        event_start_at=NOW,
    )
    completed = occurrence(
        owner_id=owner_id,
        due_at=NOW - timedelta(days=1),
        event_start_at=NOW + timedelta(hours=1),
        state="completed",
    )

    command = resumable_access_occurrences(
        owner_id,
        "paid-period-1",
        (future_event, not_due, event_started, completed),
        at=NOW,
    )

    assert command.owner_id == owner_id
    assert command.period_id == "paid-period-1"
    assert command.occurrences == (future_event,)


def test_access_resume_rejects_a_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resumable_access_occurrences(uuid4(), "period", (), at=datetime(2026, 10, 14))


def test_access_resume_rejects_a_foreign_schedule_snapshot() -> None:
    with pytest.raises(ValueError, match="foreign owner"):
        resumable_access_occurrences(
            uuid4(),
            "period",
            (occurrence(owner_id=uuid4(), due_at=NOW, event_start_at=NOW + timedelta(hours=1)),),
            at=NOW,
        )
