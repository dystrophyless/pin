from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pin.contexts.notifications.quiet_hours_api import defer_until_quiet_hours_end

ZONE = "America/New_York"
START = time(22)
END = time(1, 30)


def _assert_future_outside_quiet(now: datetime, deferred: datetime, end: time = END) -> None:
    zone = ZoneInfo(ZONE)
    local = deferred.astimezone(zone).time().replace(tzinfo=None)
    assert deferred > now
    assert not (local >= START or local < end)


def _defer(now: datetime, *, end: time = END) -> datetime:
    return defer_until_quiet_hours_end(
        now,
        zone_name=ZONE,
        quiet_start=START,
        quiet_end=end,
    )


def test_second_fold_defers_to_the_future_occurrence_of_quiet_end() -> None:
    now = datetime(2026, 11, 1, 6, 15, tzinfo=UTC)  # second 01:15 in New York

    deferred = _defer(now)

    assert deferred == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    _assert_future_outside_quiet(now, deferred)


def test_first_fold_defers_through_the_repeated_quiet_interval() -> None:
    now = datetime(2026, 11, 1, 5, 15, tzinfo=UTC)  # first 01:15 in New York

    deferred = _defer(now)

    assert deferred == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    _assert_future_outside_quiet(now, deferred)


def test_ordinary_overnight_boundary_uses_the_local_end() -> None:
    now = datetime(2026, 10, 14, 4, 15, tzinfo=UTC)  # 00:15 in New York

    deferred = _defer(now)

    assert deferred == datetime(2026, 10, 14, 5, 30, tzinfo=UTC)
    _assert_future_outside_quiet(now, deferred)


def test_nonexistent_spring_quiet_end_keeps_forward_gap_interpretation() -> None:
    now = datetime(2026, 3, 8, 6, 45, tzinfo=UTC)  # 01:45 before the spring gap

    deferred = _defer(now, end=time(2, 30))

    assert deferred == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 after the gap
    _assert_future_outside_quiet(now, deferred, end=time(2, 30))
