from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from pin.contexts.notifications.api import (
    OmittedReminder,
    ReminderCandidate,
    ReminderNoticeKind,
    project_reminders,
)

NOW = datetime(2026, 10, 14, 7, tzinfo=UTC)
ZONE = "Asia/Qyzylorda"


def candidate(
    kind: str, amount: int, source: str, due_at: datetime
) -> ReminderCandidate:
    return ReminderCandidate(kind, amount, source, due_at)


def project(
    reminders: tuple[ReminderCandidate, ...],
    *,
    event_start: datetime,
    omitted: tuple[OmittedReminder, ...] = (),
    quiet: tuple[time, time] = (time(22), time(8)),
    now: datetime = NOW,
):
    return project_reminders(
        reminders,
        omitted,
        event_start_at=event_start,
        now=now,
        quiet_zone=ZONE,
        quiet_start=quiet[0],
        quiet_end=quiet[1],
    )


def test_preview_projection_preserves_calendar_and_explicit_elapsed_instants() -> None:
    thursday = datetime(2026, 10, 15, 9, tzinfo=UTC)  # 14:00 in the home zone
    friday = datetime(2026, 10, 16, 7, tzinfo=UTC)  # explicit 12:00 in the home zone
    result = project(
        (
            candidate("calendar_days", 1, "default", thursday),
            candidate("elapsed_seconds", 7200, "explicit", friday),
        ),
        event_start=datetime(2026, 10, 16, 9, tzinfo=UTC),
    )

    observed = [
        (item.offset_kind, item.offset_amount, item.source, item.due_at)
        for item in result.scheduled
    ]
    assert observed == [
        ("calendar_days", 1, "default", thursday),
        ("elapsed_seconds", 7200, "explicit", friday),
    ]
    assert result.omitted == ()
    assert result.notices == ()


def test_default_inside_quiet_hours_defers_to_local_end_when_still_useful() -> None:
    due = datetime(2026, 10, 15, 19, tzinfo=UTC)  # 00:00 local, Fri
    start = datetime(2026, 10, 16, 8, tzinfo=UTC)  # 13:00 local, Fri
    result = project(
        (candidate("elapsed_seconds", 3600, "default", due),),
        event_start=start,
    )

    assert len(result.scheduled) == 1
    assert result.scheduled[0].due_at == datetime(2026, 10, 16, 3, tzinfo=UTC)
    assert result.scheduled[0].source == "default"
    assert result.notices[0].kind is ReminderNoticeKind.QUIET_DEFERRED
    assert result.notices[0].original_due_at == due


def test_default_is_omitted_when_quiet_deferral_would_reach_event_start() -> None:
    due = datetime(2026, 10, 15, 19, tzinfo=UTC)  # 00:00 local, Fri
    start = datetime(2026, 10, 15, 23, 30, tzinfo=UTC)  # 05:30 local
    result = project((candidate("elapsed_seconds", 1800, "default", due),), event_start=start)

    assert result.scheduled == ()
    assert len(result.omitted) == 1
    assert result.omitted[0].reason == "quiet_unusable"
    assert result.omitted[0].due_at == due


def test_explicit_quiet_conflict_keeps_requested_instant_and_exposes_notice() -> None:
    due = datetime(2026, 10, 15, 19, tzinfo=UTC)
    result = project(
        (candidate("elapsed_seconds", 3600, "explicit", due),),
        event_start=datetime(2026, 10, 15, 22, tzinfo=UTC),
    )

    assert result.scheduled[0].due_at == due
    assert result.scheduled[0].source == "explicit"
    assert result.notices[0].kind is ReminderNoticeKind.QUIET_CONFLICT
    assert result.notices[0].due_at == due


def test_nonquiet_default_and_prior_past_omission_are_preserved() -> None:
    due = datetime(2026, 10, 15, 10, tzinfo=UTC)
    past = datetime(2026, 10, 13, 7, tzinfo=UTC)
    old_omission = OmittedReminder("calendar_days", 1, "default", past, "past")
    result = project(
        (candidate("elapsed_seconds", 3600, "default", due),),
        event_start=datetime(2026, 10, 16, 12, tzinfo=UTC),
        omitted=(old_omission,),
    )

    assert result.scheduled[0].due_at == due
    assert result.omitted == (old_omission,)
    assert result.notices == ()


def test_projection_rejects_naive_clock_and_duplicate_semantic_offset() -> None:
    due = datetime(2026, 10, 15, 10, tzinfo=UTC)
    item = candidate("elapsed_seconds", 3600, "default", due)
    with pytest.raises(ValueError, match="timezone-aware"):
        project((item,), event_start=due + timedelta(days=1), now=datetime(2026, 10, 14))
    with pytest.raises(ValueError, match="unique"):
        project((item, item), event_start=due + timedelta(days=1))
