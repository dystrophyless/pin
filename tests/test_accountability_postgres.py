from __future__ import annotations

import os
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.accountability import (
    process_accountability_action,
    render_accountability_delivery,
)
from pin.contexts.accountability.api import (
    AccountabilitySend,
    CurrentOccurrence,
    DailyReviewPage,
    MessageSlot,
    OpenUntimedItem,
    ReviewProfile,
    get_daily_review_page,
    schedule_due_accountability,
    synchronize_occurrence,
)
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityIntentRecord,
    CheckInCycleRecord,
    DailyReviewRecord,
)
from pin.contexts.accountability.infrastructure.scheduler import apply_delivery_outcome
from pin.persistence import run_local_async
from pin.persistence.db import create_engine

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue16_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


class _TodayItems:
    def __init__(self, items: tuple[OpenUntimedItem, ...]) -> None:
        self.items = items

    async def list_today_open_untimed(
        self,
        _owner_id: UUID,
        *,
        profile_zone: str,
        at: datetime,
        for_update: bool = False,
    ) -> tuple[OpenUntimedItem, ...]:
        del profile_zone, at, for_update
        return self.items


def _database_url() -> str:
    value = os.getenv("PIN_TEST_ACCOUNTABILITY_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_ACCOUNTABILITY_DATABASE_URL to the fresh issue-16 PostgreSQL database"
        )
    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            name = connection.exec_driver_sql("select current_database()").scalar_one()
            user = connection.exec_driver_sql("select current_user").scalar_one()
            port = connection.exec_driver_sql("show port").scalar_one()
            directory = connection.exec_driver_sql("show data_directory").scalar_one()
            version = connection.exec_driver_sql("select version()").scalar_one()
    finally:
        engine.dispose()
    if not str(name).startswith(DATABASE_PREFIX):
        pytest.fail("Accountability test database name is outside the issue-16 prefix")
    if user != "pin_test" or int(port) != DATABASE_PORT:
        pytest.fail("Accountability PostgreSQL user or port does not match the assigned fixture")
    if str(directory).replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail(
            "Accountability PostgreSQL data directory is not the assigned disposable cluster"
        )
    if "PostgreSQL 18.6" not in str(version):
        pytest.fail("Accountability PostgreSQL version does not match the assigned fixture")
    return value


@pytest.fixture(scope="session")
def accountability_database() -> Iterator[str]:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    yield value


def _run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    run_local_async(main)


def test_known_end_confirmed_send_starts_one_nudge_then_exhausts(
    accountability_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(accountability_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id = uuid4(), uuid4()
        profile = ReviewProfile(
            owner_id=owner_id,
            home_zone="Asia/Qyzylorda",
            language="en",
            review_time=time(20),
            quiet_start=time(23),
            quiet_end=time(7),
            destination_id=987654321,
        )
        now = datetime(2026, 10, 14, 15, tzinfo=UTC)
        occurrence = CurrentOccurrence(
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            occurrence_version=1,
            kind="appointment",
            state="open",
            title="Dentist",
            event_zone="Asia/Qyzylorda",
            local_date=date(2026, 10, 14),
            starts_at=now - timedelta(hours=1),
            ends_at=now - timedelta(minutes=10),
            deadline_at=None,
        )
        async with sessions() as session, session.begin():
            unknown_end = replace(occurrence, occurrence_id=uuid4(), ends_at=None)
            assert await synchronize_occurrence(session, unknown_end, now=now) is None
            cycle = await synchronize_occurrence(session, occurrence, now=now)
            assert cycle is not None
            created = await schedule_due_accountability(
                session,
                profiles=(profile,),
                today_untimed=_TodayItems(()),
                now=now,
            )
            assert created == 1
            initial = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.cycle_id == cycle.id,
                    AccountabilityIntentRecord.slot == "initial",
                )
            )
            assert initial is not None and initial.state == "scheduled"
            initial_id = initial.id
            cycle_id = cycle.id
            unknown_occurrence = replace(
                occurrence,
                occurrence_id=uuid4(),
                title="Unconfirmed appointment",
                ends_at=now - timedelta(minutes=5),
            )
            unknown_cycle = await synchronize_occurrence(session, unknown_occurrence, now=now)
            assert unknown_cycle is not None
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems(()),
                    now=now,
                )
                == 1
            )
            unknown_initial = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.cycle_id == unknown_cycle.id,
                    AccountabilityIntentRecord.slot == "initial",
                )
            )
            assert unknown_initial is not None
            unknown_initial_id = unknown_initial.id
            unknown_cycle_id = unknown_cycle.id

        confirmed_at = now + timedelta(minutes=3)
        async with sessions() as session, session.begin():
            assert await apply_delivery_outcome(
                session,
                event_id=uuid4(),
                owner_id=owner_id,
                intent_id=initial_id,
                outcome="confirmed",
                occurred_at=confirmed_at,
                attempt_number=1,
            )
            assert await apply_delivery_outcome(
                session,
                event_id=uuid4(),
                owner_id=owner_id,
                intent_id=unknown_initial_id,
                outcome="unknown",
                occurred_at=confirmed_at,
                attempt_number=1,
            )
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems(()),
                    now=confirmed_at + timedelta(minutes=59),
                )
                == 0
            )

        nudge_due = confirmed_at + timedelta(hours=1)
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems(()),
                    now=nudge_due,
                )
                == 1
            )
            nudge = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.cycle_id == cycle_id,
                    AccountabilityIntentRecord.slot == "nudge",
                )
            )
            assert nudge is not None and nudge.state == "scheduled"
            nudge_id = nudge.id
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems(()),
                    now=nudge_due + timedelta(hours=4),
                )
                == 0
            )
            assert await apply_delivery_outcome(
                session,
                event_id=uuid4(),
                owner_id=owner_id,
                intent_id=nudge_id,
                outcome="unknown",
                occurred_at=nudge_due + timedelta(hours=4),
                attempt_number=1,
            )
        async with sessions() as session:
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            assert cycle is not None and cycle.state == "exhausted"
            assert cycle.nudge_slot_used is True
            replayed = await synchronize_occurrence(
                session,
                occurrence,
                now=nudge_due + timedelta(days=1),
            )
            assert replayed is not None and replayed.id == cycle_id
            assert replayed.state == "exhausted"
            cycle_count = await session.scalar(
                select(func.count())
                .select_from(CheckInCycleRecord)
                .where(
                    CheckInCycleRecord.owner_id == owner_id,
                    CheckInCycleRecord.occurrence_id == occurrence_id,
                )
            )
            assert cycle_count == 1
            unconfirmed_cycle = await session.get(CheckInCycleRecord, unknown_cycle_id)
            assert unconfirmed_cycle is not None
            assert unconfirmed_cycle.initial_confirmed_at is None
            assert unconfirmed_cycle.nudge_due_at is None
            assert (
                await session.scalar(
                    select(AccountabilityIntentRecord.id).where(
                        AccountabilityIntentRecord.cycle_id == unknown_cycle_id,
                        AccountabilityIntentRecord.slot == "nudge",
                    )
                )
                is None
            )
        await engine.dispose()

    _run(scenario)


def test_daily_reviews_group_today_and_create_a_new_cycle_next_profile_day(
    accountability_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(accountability_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id = uuid4()
        profile = ReviewProfile(
            owner_id=owner_id,
            home_zone="Asia/Qyzylorda",
            language="ru",
            review_time=time(20),
            quiet_start=time(23),
            quiet_end=time(7),
            destination_id=987654321,
        )
        first_day = date(2026, 10, 14)
        first_now = datetime(2026, 10, 14, 15, tzinfo=UTC)
        same_day_item = OpenUntimedItem(uuid4(), "Read chapter", first_day)
        other_day_item = OpenUntimedItem(uuid4(), "Tomorrow task", first_day + timedelta(days=1))
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems((same_day_item, other_day_item)),
                    now=first_now,
                )
                == 1
            )
            review = await session.scalar(
                select(DailyReviewRecord).where(
                    DailyReviewRecord.owner_id == owner_id,
                    DailyReviewRecord.review_date == first_day,
                )
            )
            assert review is not None
            assert review.occurrence_ids == [str(same_day_item.occurrence_id)]
            first_review_id = review.id
            initial = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.review_id == review.id,
                    AccountabilityIntentRecord.slot == "initial",
                )
            )
            assert initial is not None
            initial_id = initial.id
        async with sessions() as session, session.begin():
            assert await apply_delivery_outcome(
                session,
                event_id=uuid4(),
                owner_id=owner_id,
                intent_id=initial_id,
                outcome="confirmed",
                occurred_at=first_now,
                attempt_number=1,
            )
        first_nudge_due = first_now + timedelta(hours=1)
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems((same_day_item, other_day_item)),
                    now=first_nudge_due,
                )
                == 1
            )
            nudge = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.review_id == first_review_id,
                    AccountabilityIntentRecord.slot == "nudge",
                )
            )
            assert nudge is not None
            assert await apply_delivery_outcome(
                session,
                event_id=uuid4(),
                owner_id=owner_id,
                intent_id=nudge.id,
                outcome="failed",
                occurred_at=first_nudge_due,
                attempt_number=1,
                retrying=False,
            )
        next_day = first_day + timedelta(days=1)
        next_now = datetime(2026, 10, 15, 15, tzinfo=UTC)
        next_item = OpenUntimedItem(uuid4(), "Pay invoice", next_day)
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems((same_day_item, other_day_item, next_item)),
                    now=next_now,
                )
                == 1
            )
            reviews = list(
                (
                    await session.scalars(
                        select(DailyReviewRecord).where(DailyReviewRecord.owner_id == owner_id)
                    )
                ).all()
            )
            assert {review.review_date for review in reviews} == {first_day, next_day}
            assert len({review.id for review in reviews}) == 2
            assert all(
                review.state != "open" for review in reviews if review.review_date == first_day
            )
            next_review = next(item for item in reviews if item.review_date == next_day)
            assert set(next_review.occurrence_ids) == {
                str(other_day_item.occurrence_id),
                str(next_item.occurrence_id),
            }
        await engine.dispose()

    _run(scenario)


def test_scheduler_filters_owner_page_and_ignores_waiting_cycles_before_due_work(
    accountability_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(accountability_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, other_owner = uuid4(), uuid4()
        profile = ReviewProfile(
            owner_id=owner_id,
            home_zone="Asia/Qyzylorda",
            language="en",
            review_time=time(20),
            quiet_start=time(23),
            quiet_end=time(7),
            destination_id=987654321,
        )
        now = datetime(2026, 10, 14, 15, tzinfo=UTC)
        async with sessions() as session, session.begin():
            session.add_all(
                CheckInCycleRecord(
                    owner_id=owner_id,
                    occurrence_id=uuid4(),
                    occurrence_version=1,
                    kind="appointment",
                    title=f"Waiting {index}",
                    expected_end_at=now - timedelta(days=2),
                    state="open",
                    initial_confirmed_at=now,
                    nudge_due_at=now + timedelta(hours=1),
                    nudge_slot_used=False,
                )
                for index in range(100)
            )
            due_cycle = CheckInCycleRecord(
                owner_id=owner_id,
                occurrence_id=uuid4(),
                occurrence_version=1,
                kind="deadline",
                title="Must not starve",
                expected_end_at=now - timedelta(minutes=1),
                state="open",
                nudge_slot_used=False,
            )
            off_page_cycle = CheckInCycleRecord(
                owner_id=other_owner,
                occurrence_id=uuid4(),
                occurrence_version=1,
                kind="deadline",
                title="Different profile page",
                expected_end_at=now - timedelta(minutes=2),
                state="open",
                nudge_slot_used=False,
            )
            session.add_all((due_cycle, off_page_cycle))
            await session.flush()
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=_TodayItems(()),
                    now=now,
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(AccountabilityIntentRecord.id).where(
                        AccountabilityIntentRecord.cycle_id == due_cycle.id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(AccountabilityIntentRecord.id).where(
                        AccountabilityIntentRecord.cycle_id == off_page_cycle.id,
                    )
                )
                is None
            )
        await engine.dispose()

    _run(scenario)


def test_large_review_pages_keep_all_current_items_accessible_and_owner_bound(
    accountability_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(accountability_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, other_owner = uuid4(), uuid4()
        profile = ReviewProfile(
            owner_id=owner_id,
            home_zone="Asia/Qyzylorda",
            language="en",
            review_time=time(20),
            quiet_start=time(23),
            quiet_end=time(7),
            destination_id=987654321,
        )
        today = date(2026, 10, 14)
        now = datetime(2026, 10, 14, 15, tzinfo=UTC)
        items = tuple(OpenUntimedItem(uuid4(), f"Item {index}", today) for index in range(25))
        reader = _TodayItems(items)
        async with sessions() as session, session.begin():
            assert (
                await schedule_due_accountability(
                    session,
                    profiles=(profile,),
                    today_untimed=reader,
                    now=now,
                )
                == 1
            )
            review = await session.scalar(
                select(DailyReviewRecord).where(
                    DailyReviewRecord.owner_id == owner_id,
                    DailyReviewRecord.review_date == today,
                )
            )
            assert review is not None
            occurrence_ids = tuple(item.occurrence_id for item in items)
            send = AccountabilitySend(
                intent_id=uuid4(),
                owner_id=owner_id,
                slot=MessageSlot.INITIAL,
                destination_id=profile.destination_id,
                language=profile.language,
                title=None,
                occurrence_ids=occurrence_ids,
                cycle_id=None,
                review_id=review.id,
                plain_text="Which items did you complete?",
            )
            occurrences = tuple(
                CurrentOccurrence(
                    owner_id=owner_id,
                    occurrence_id=item.occurrence_id,
                    occurrence_version=1,
                    kind="untimed",
                    state="open",
                    title=item.title,
                    event_zone=profile.home_zone,
                    local_date=today,
                    starts_at=None,
                    ends_at=None,
                    deadline_at=None,
                )
                for item in items
            )
            first_text, first_keyboard = await render_accountability_delivery(
                session, send, occurrences=occurrences, now=now
            )
            assert "Page 1 of 3" in first_text
            first_rows = cast(list[list[dict[str, str]]], first_keyboard["inline_keyboard"])
            assert len(first_rows) == 11
            next_callback = first_rows[-1][0]["callback_data"]
            wrong_owner = await process_accountability_action(
                session,
                owner_id=other_owner,
                data=next_callback,
                language="en",
                now=now,
                today_untimed=reader,
            )
            assert wrong_owner is not None and "invalid or expired" in wrong_owner.text
            second = await process_accountability_action(
                session,
                owner_id=owner_id,
                data=next_callback,
                language="en",
                now=now,
                today_untimed=reader,
            )
            assert second is not None and "Page 2 of 3" in second.text
            assert second.keyboard is not None
            second_rows = cast(list[list[dict[str, str]]], second.keyboard["inline_keyboard"])
            assert len(second_rows) == 11
            replay = await process_accountability_action(
                session,
                owner_id=owner_id,
                data=next_callback,
                language="en",
                now=now,
                today_untimed=reader,
            )
            assert replay is not None and "invalid or expired" in replay.text
        async with sessions() as session:
            final_page = await get_daily_review_page(
                session,
                owner_id=owner_id,
                review_id=review.id,
                page_number=2,
                today_untimed=reader,
                now=now,
            )
            assert isinstance(final_page, DailyReviewPage)
            assert final_page.total_items == 25 and final_page.page_count == 3
            assert len(final_page.items) == 5
            stale = await get_daily_review_page(
                session,
                owner_id=owner_id,
                review_id=review.id,
                page_number=0,
                today_untimed=reader,
                now=now + timedelta(days=1),
            )
            assert stale is None
        await engine.dispose()

    _run(scenario)
