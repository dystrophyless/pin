from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from pin.contexts.planning.application.recurrence_contracts import (
    SeriesOccurrenceSnapshot,
    SeriesSnapshot,
)
from pin.contexts.planning.application.recurrence_exceptions import preview_recurrence_exception
from pin.contexts.planning.domain.plans import (
    OneOffPlanDraft,
    PlanKind,
    ScheduleNeedsClarification,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
    recurrence_date,
)


def test_monthly_day_31_clamps_then_returns_to_original_day() -> None:
    rule = RecurrenceRule(RecurrenceFrequency.MONTHLY, monthly_day=31, count=4)
    anchor = date(2027, 1, 31)
    assert [recurrence_date(anchor, rule, step) for step in range(4)] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


def test_monthly_last_day_stays_last_day_across_short_and_long_months() -> None:
    rule = RecurrenceRule(RecurrenceFrequency.MONTHLY, last_day=True, count=4)
    anchor = date(2027, 1, 31)
    assert [recurrence_date(anchor, rule, step) for step in range(4)] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


def test_weekly_multiple_weekdays_and_interval_are_calendar_based() -> None:
    rule = RecurrenceRule(
        RecurrenceFrequency.WEEKLY,
        interval=2,
        weekdays=(0, 2),
        count=4,
    )
    anchor = date(2027, 6, 7)
    assert [recurrence_date(anchor, rule, step) for step in range(4)] == [
        date(2027, 6, 7),
        date(2027, 6, 9),
        date(2027, 6, 21),
        date(2027, 6, 23),
    ]


def test_interval_must_be_positive_but_finite_stop_conditions_are_optional() -> None:
    with pytest.raises(ValueError, match="positive"):
        RecurrenceRule(RecurrenceFrequency.DAILY, interval=0, count=2)
    rule = RecurrenceRule(RecurrenceFrequency.DAILY)
    assert rule.count is None and rule.end_date is None


def test_unbounded_projection_honors_batch_limit_and_reports_more_in_window() -> None:
    start = date(2027, 1, 1)
    draft = RecurringPlanDraft(
        PlanKind.TASK,
        "Daily review",
        start,
        time(10),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.DAILY),
    )
    projection = project_recurrence(
        draft,
        owner_id=uuid4(),
        default_zone="UTC",
        default_reminders=(),
        at=datetime(2026, 12, 31, tzinfo=UTC),
        through=start + timedelta(days=10),
        max_occurrences=2,
    )
    assert len(projection.occurrences) == 2
    assert projection.truncated


def test_recurring_dst_gap_moves_forward_by_the_gap_and_fold_uses_earlier() -> None:
    spring = RecurringPlanDraft(
        PlanKind.TASK,
        "Daily check",
        date(2027, 3, 7),
        time(2, 30),
        "America/New_York",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(6,), count=2),
    )
    spring_projection = project_recurrence(
        spring,
        owner_id=uuid4(),
        default_zone="UTC",
        default_reminders=(),
        at=datetime(2027, 2, 1, tzinfo=UTC),
        through=date(2027, 3, 20),
    )
    adjusted = spring_projection.occurrences[1]
    assert adjusted.schedule.local_time == time(3, 30)
    assert adjusted.dst_gap_shift_seconds == 3600

    fall = RecurringPlanDraft(
        PlanKind.TASK,
        "Daily check",
        date(2027, 10, 31),
        time(1, 30),
        "America/New_York",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(6,), count=2),
    )
    fall_projection = project_recurrence(
        fall,
        owner_id=uuid4(),
        default_zone="UTC",
        default_reminders=(),
        at=datetime(2027, 10, 1, tzinfo=UTC),
        through=date(2027, 11, 10),
    )
    repeated = fall_projection.occurrences[1]
    assert repeated.repeated_time_choice == "earlier"
    assert repeated.schedule.starts_at == datetime(2027, 11, 7, 5, 30, tzinfo=UTC)


def test_recurrence_preview_requires_a_clarification_when_first_event_is_past() -> None:
    draft = RecurringPlanDraft(
        PlanKind.TASK,
        "Daily check",
        date(2027, 1, 1),
        time(10),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.DAILY, count=2),
    )
    with pytest.raises(ScheduleNeedsClarification):
        project_recurrence(
            draft,
            owner_id=uuid4(),
            default_zone="UTC",
            default_reminders=(),
            at=datetime(2027, 1, 2, tzinfo=UTC),
            through=date(2027, 2, 1),
        )


def _snapshot(
    draft: RecurringPlanDraft,
    *,
    count: int,
    terminal_sequence: int | None = None,
) -> SeriesSnapshot:
    owner_id = uuid4()
    occurrences = tuple(
        SeriesOccurrenceSnapshot(
            sequence=sequence,
            occurrence_id=uuid4(),
            recurrence_key=f"r1:{sequence}",
            state="completed" if sequence == terminal_sequence else "open",
            version=1,
            local_date=recurrence_date(draft.start_date, draft.recurrence, sequence),
        )
        for sequence in range(count)
    )
    return SeriesSnapshot(
        id=uuid4(),
        owner_id=owner_id,
        plan_item_id=uuid4(),
        version=4,
        state="active",
        anchor_date=draft.start_date,
        anchor_sequence=0,
        next_sequence=count,
        total_count=draft.recurrence.count,
        end_date=draft.recurrence.end_date,
        draft=draft,
        effective_reminders=(),
        default_zone="UTC",
        occurrences=occurrences,
    )


def test_this_and_future_reanchors_with_remaining_total_count_and_history() -> None:
    original = RecurringPlanDraft(
        PlanKind.TASK,
        "Class",
        date(2026, 6, 1),
        time(9),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0,), count=10),
    )
    snapshot = _snapshot(original, count=10)
    change = RecurrenceExceptionDraft(
        RecurrenceExceptionScope.THIS_AND_FUTURE,
        4,
        OneOffPlanDraft(PlanKind.TASK, "Class", date(2026, 6, 3), time(9), "UTC"),
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,), count=10),
    )
    preview = preview_recurrence_exception(
        snapshot,
        change,
        owner_id=snapshot.owner_id,
        at=datetime(2026, 5, 1, tzinfo=UTC),
        through=date(2027, 6, 3),
    )
    assert preview.remaining_count == 6
    assert [item.schedule.local_date for item in preview.projection.occurrences] == [
        date(2026, 6, 3),
        date(2026, 6, 10),
        date(2026, 6, 17),
        date(2026, 6, 24),
        date(2026, 7, 1),
        date(2026, 7, 8),
    ]
    assert [item.local_date for item in snapshot.occurrences[:4]] == [
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
        date(2026, 6, 22),
    ]


def test_interval_series_move_restarts_interval_from_new_date() -> None:
    original = RecurringPlanDraft(
        PlanKind.TASK,
        "Biweekly",
        date(2026, 6, 1),
        time(9),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, interval=2, weekdays=(0,), count=3),
    )
    snapshot = _snapshot(original, count=3)
    change = RecurrenceExceptionDraft(
        RecurrenceExceptionScope.THIS_AND_FUTURE,
        0,
        OneOffPlanDraft(PlanKind.TASK, "Biweekly", date(2026, 6, 8), time(9), "UTC"),
    )
    preview = preview_recurrence_exception(
        snapshot,
        change,
        owner_id=snapshot.owner_id,
        at=datetime(2026, 5, 1, tzinfo=UTC),
        through=date(2027, 6, 9),
    )
    assert [item.schedule.local_date for item in preview.projection.occurrences] == [
        date(2026, 6, 8),
        date(2026, 6, 22),
        date(2026, 7, 6),
    ]
    assert preview.remaining_count == 3


def test_single_occurrence_exception_does_not_reanchor_series() -> None:
    original = RecurringPlanDraft(
        PlanKind.TASK,
        "Class",
        date(2026, 6, 1),
        time(9),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0,), count=3),
    )
    snapshot = _snapshot(original, count=3)
    change = RecurrenceExceptionDraft(
        RecurrenceExceptionScope.SINGLE,
        1,
        OneOffPlanDraft(PlanKind.TASK, "Class", date(2026, 6, 10), time(10), "UTC"),
    )
    preview = preview_recurrence_exception(
        snapshot,
        change,
        owner_id=snapshot.owner_id,
        at=datetime(2026, 5, 1, tzinfo=UTC),
        through=date(2026, 6, 11),
    )
    assert [item.schedule.local_date for item in preview.projection.occurrences] == [
        date(2026, 6, 10)
    ]
    assert preview.updated_draft.start_date == date(2026, 6, 1)
    assert preview.updated_draft.recurrence.weekdays == (0,)


def test_exception_cannot_reopen_terminal_history() -> None:
    original = RecurringPlanDraft(
        PlanKind.TASK,
        "Class",
        date(2026, 6, 1),
        time(9),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0,), count=3),
    )
    snapshot = _snapshot(original, count=3, terminal_sequence=1)
    change = RecurrenceExceptionDraft(
        RecurrenceExceptionScope.THIS_AND_FUTURE,
        0,
        OneOffPlanDraft(PlanKind.TASK, "Class", date(2026, 6, 8), time(9), "UTC"),
    )
    with pytest.raises(ValueError, match="cannot rewrite completed or cancelled history"):
        preview_recurrence_exception(
            snapshot,
            change,
            owner_id=snapshot.owner_id,
            at=datetime(2026, 5, 1, tzinfo=UTC),
            through=date(2027, 6, 9),
        )
