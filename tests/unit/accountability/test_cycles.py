from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from pin.contexts.accountability.domain.cycles import (
    CheckInCycle,
    CycleKind,
    CycleState,
    DailyReview,
    appointment_check_due,
    eligible_nudge_at,
    review_due_at,
)
from pin.contexts.accountability.domain.reviews import ReviewItem, review_items_for_today


def test_initial_confirmed_outcome_alone_starts_timer_at_original_delivery_time() -> None:
    delivered = datetime(2026, 10, 14, 12, tzinfo=UTC)
    cycle = _cycle()
    assert cycle.initial_confirmed_at is None

    confirmed = cycle.confirmed_initial(intent_id=uuid4(), confirmed_at=delivered)
    assert confirmed.initial_confirmed_at == delivered
    assert confirmed.nudge_due_at == delivered + timedelta(hours=1)
    assert eligible_nudge_at(confirmed, now=delivered + timedelta(minutes=59)) is None
    assert eligible_nudge_at(
        confirmed, now=delivered + timedelta(hours=1)
    ) == delivered + timedelta(hours=1)


def test_appointment_needs_a_persisted_end_and_deadline_uses_persisted_deadline() -> None:
    persisted = datetime(2026, 10, 14, 15, tzinfo=UTC)
    assert appointment_check_due(kind="appointment", end_at=None, deadline_at=None) is None
    assert (
        appointment_check_due(kind="appointment", end_at=persisted, deadline_at=None) == persisted
    )
    assert appointment_check_due(kind="deadline", end_at=None, deadline_at=persisted) == persisted
    assert appointment_check_due(kind="untimed", end_at=None, deadline_at=None) is None


def test_nudge_slot_is_single_and_terminal_cycles_do_not_reopen() -> None:
    initial = datetime(2026, 10, 14, 12, tzinfo=UTC)
    due = _cycle().confirmed_initial(intent_id=uuid4(), confirmed_at=initial)
    nudge_id = uuid4()
    claimed = due.claim_nudge(intent_id=nudge_id)
    assert claimed.nudge_slot_used is True
    assert claimed.nudge_intent_id == nudge_id
    assert claimed.claim_nudge(intent_id=uuid4()) == claimed
    exhausted = replace(
        claimed,
        state=CycleState.EXHAUSTED,
    )
    assert eligible_nudge_at(exhausted, now=initial + timedelta(days=3)) is None


def test_local_review_time_uses_profile_zone_and_item_group_is_deduplicated() -> None:
    due = review_due_at(date(2026, 10, 14), time(20), "Asia/Qyzylorda")
    assert due == datetime(2026, 10, 14, 15, tzinfo=UTC)
    first, second = uuid4(), uuid4()
    items = (
        ReviewItem(first, "Review project"),
        ReviewItem(second, "Send draft"),
        ReviewItem(first, "Review project"),
    )
    assert review_items_for_today(items) == items[:2]


def test_daily_review_has_one_owner_date_and_requires_unique_occurrences() -> None:
    occurrence = uuid4()
    review = DailyReview(
        id=uuid4(),
        owner_id=uuid4(),
        review_date=date(2026, 10, 14),
        zone_name="UTC",
        occurrence_ids=(occurrence,),
        due_at=datetime(2026, 10, 14, 20, tzinfo=UTC),
    )
    assert review.occurrence_ids == (occurrence,)
    with pytest.raises(ValueError, match="unique"):
        DailyReview(
            id=review.id,
            owner_id=review.owner_id,
            review_date=review.review_date,
            zone_name=review.zone_name,
            occurrence_ids=(occurrence, occurrence),
            due_at=review.due_at,
        )


def _cycle() -> CheckInCycle:
    return CheckInCycle(
        id=uuid4(),
        owner_id=uuid4(),
        occurrence_id=uuid4(),
        occurrence_version=1,
        kind=CycleKind.APPOINTMENT,
        expected_end_at=datetime(2026, 10, 14, 12, tzinfo=UTC),
    )
