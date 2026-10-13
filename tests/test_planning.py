from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from pin.contexts.planning.application.preview import preview_plan_batch
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
    ScheduleNeedsClarification,
    payload_digest,
    resolve_reminders,
    resolve_schedule,
)

NOW = datetime(2026, 10, 13, 8, tzinfo=UTC)


def test_appointment_requires_explicit_end_or_duration() -> None:
    with pytest.raises(ValueError, match="explicit end or duration"):
        OneOffPlanDraft(PlanKind.APPOINTMENT, "Check-up", date(2026, 10, 14), time(14))
    with pytest.raises(ValueError, match="explicit end or duration"):
        OneOffPlanDraft(
            PlanKind.APPOINTMENT,
            "Check-up",
            date(2026, 10, 14),
            time(14),
            end_local_time=time(15),
            duration_seconds=3600,
        )


def test_appointment_end_must_follow_start_without_guessing_midnight() -> None:
    draft = OneOffPlanDraft(
        PlanKind.APPOINTMENT,
        "Check-up",
        date(2026, 10, 14),
        time(14),
        end_local_time=time(13),
        event_zone="Asia/Almaty",
    )
    with pytest.raises(ValueError, match="after its start"):
        resolve_schedule(draft, default_zone="UTC", now=NOW)


def test_nonexistent_and_repeated_one_off_times_require_resolution() -> None:
    gap = OneOffPlanDraft(PlanKind.TASK, "Walk", date(2026, 3, 29), time(2, 30), "Europe/Berlin")
    with pytest.raises(ScheduleNeedsClarification, match="daylight-saving gap"):
        resolve_schedule(gap, default_zone="UTC", now=NOW)

    repeated = OneOffPlanDraft(
        PlanKind.TASK, "Walk", date(2026, 10, 25), time(2, 30), "Europe/Berlin"
    )
    with pytest.raises(ScheduleNeedsClarification, match="choose earlier or later"):
        resolve_schedule(repeated, default_zone="UTC", now=NOW)
    earlier = resolve_schedule(replace(repeated, fold="earlier"), default_zone="UTC", now=NOW)
    later = resolve_schedule(replace(repeated, fold="later"), default_zone="UTC", now=NOW)
    assert earlier.starts_at is not None and later.starts_at is not None
    assert later.starts_at - earlier.starts_at == timedelta(hours=1)


@pytest.mark.parametrize(
    ("event_date", "delta"),
    [(date(2026, 3, 29), -timedelta(hours=1)), (date(2026, 10, 25), timedelta(hours=1))],
)
def test_calendar_day_and_elapsed_day_keep_distinct_meaning(
    event_date: date, delta: timedelta
) -> None:
    now = datetime.combine(event_date - timedelta(days=2), time(8), UTC)
    schedule = resolve_schedule(
        OneOffPlanDraft(
            PlanKind.APPOINTMENT,
            "Appointment",
            event_date,
            time(9),
            "Europe/Berlin",
            duration_seconds=3600,
        ),
        default_zone="UTC",
        now=now,
    )
    reminders = resolve_reminders(
        schedule,
        (
            ReminderOffset(OffsetKind.CALENDAR_DAYS, 1),
            ReminderOffset(OffsetKind.ELAPSED_SECONDS, 86400),
        ),
        now=now,
    )
    assert reminders[1].due_at - reminders[0].due_at == delta


def test_untimed_retains_local_date_without_an_instant() -> None:
    schedule = resolve_schedule(
        OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 14)),
        default_zone="Asia/Almaty",
        now=NOW,
    )
    assert schedule.local_date == date(2026, 10, 14)
    assert schedule.local_time is None
    assert schedule.starts_at is schedule.deadline_at is None


def test_past_explicit_time_and_untimed_day_need_clarification() -> None:
    past = OneOffPlanDraft(PlanKind.TASK, "Task", date(2026, 10, 13), time(7), "UTC")
    with pytest.raises(ScheduleNeedsClarification, match="in the past"):
        resolve_schedule(past, default_zone="UTC", now=NOW)
    old_day = OneOffPlanDraft(PlanKind.UNTIMED, "Task", date(2026, 10, 12), event_zone="UTC")
    with pytest.raises(ScheduleNeedsClarification, match="past local date"):
        resolve_schedule(old_day, default_zone="UTC", now=NOW)


def test_approved_payload_digest_changes_with_any_schedule_field() -> None:
    draft = OneOffPlanDraft(PlanKind.TASK, "Walk", date(2026, 10, 14), time(9), reminders=())
    changed = OneOffPlanDraft(PlanKind.TASK, "Walk", date(2026, 10, 14), time(10), reminders=())
    assert payload_digest((draft,), expected_preferences_version=1) != payload_digest(
        (changed,), expected_preferences_version=1
    )


def test_preview_fingerprint_binds_effective_reminders_but_excludes_generated_ids() -> None:
    draft = OneOffPlanDraft(PlanKind.TASK, "Walk", date(2026, 10, 14), time(9), "UTC")
    default = (ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "default"),)
    preview = preview_plan_batch(
        uuid4(),
        (draft,),
        expected_preferences_version=2,
        default_zone="UTC",
        default_reminders=default,
        at=NOW,
    )
    same_intent = preview_plan_batch(
        uuid4(),
        (draft,),
        expected_preferences_version=2,
        default_zone="UTC",
        default_reminders=default,
        at=NOW + timedelta(minutes=1),
    )
    assert preview.plans[0].item.id != same_intent.plans[0].item.id
    assert preview.approval_projection_sha256 == same_intent.approval_projection_sha256

    reminder_now_past = preview_plan_batch(
        uuid4(),
        (draft,),
        expected_preferences_version=2,
        default_zone="UTC",
        default_reminders=default,
        at=datetime(2026, 10, 14, 8, 30, tzinfo=UTC),
    )
    assert reminder_now_past.plans[0].occurrence.schedule.starts_at > datetime(
        2026, 10, 14, 8, 30, tzinfo=UTC
    )
    assert not reminder_now_past.plans[0].reminders
    assert len(reminder_now_past.plans[0].omitted_reminders) == 1
    assert preview.approval_projection_sha256 != reminder_now_past.approval_projection_sha256
