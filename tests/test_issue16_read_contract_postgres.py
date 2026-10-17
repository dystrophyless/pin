from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.contexts.identity.api import PreferencesPage, list_review_profiles
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.planning.api import get_accountability_occurrence, list_today_open_untimed
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "PIN_TEST_ISSUE16_READ_DATABASE_URL"
DATABASE_PREFIX = "pin_issue16_read_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
DATABASE_USER = "pin_test"
DATABASE_VERSION = "18.6"


@pytest.fixture(scope="module")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def _guarded_database_url() -> str:
    value = os.getenv(DATABASE_ENV)
    if not value:
        pytest.skip(f"set {DATABASE_ENV} to the assigned disposable Issue16 read fixture")
    url = make_url(value)
    if url.host != "127.0.0.1" or url.port != DATABASE_PORT or url.username != DATABASE_USER:
        pytest.fail("Issue16 read PostgreSQL URL must target the assigned host, port, and user")
    if not url.database or not url.database.startswith(DATABASE_PREFIX):
        pytest.fail("Issue16 read PostgreSQL database name must use the unique assigned prefix")
    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
            version = str(connection.exec_driver_sql("show server_version").scalar_one())
    finally:
        engine.dispose()
    if name != url.database or not name.startswith(DATABASE_PREFIX):
        pytest.fail("connected PostgreSQL database does not match the unique Issue16 fixture name")
    if name == "pin_model_adapter_20261013":
        pytest.fail(
            "Issue16 reads must use their own unique fixture, never the model-adapter database"
        )
    if port != DATABASE_PORT or user != DATABASE_USER or version != DATABASE_VERSION:
        pytest.fail("connected PostgreSQL server does not match the assigned Issue16 fixture")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("connected PostgreSQL data directory is not the assigned disposable cluster")
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


@pytest.fixture(scope="module")
def issue16_read_database_url() -> str:
    return _guarded_database_url()


@pytest_asyncio.fixture(scope="module")
async def sessions(
    issue16_read_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(issue16_read_database_url)
    try:
        yield async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _profile(owner_id: UUID) -> PreferencesRecord:
    return PreferencesRecord(
        owner_id=owner_id,
        language="en",
        home_zone="UTC",
        reminder_calendar_days=2,
        reminder_elapsed_hours=3,
        quiet_start=time(22),
        quiet_end=time(7),
        review_time=time(9),
        version=4,
    )


async def _cleanup(
    sessions: async_sessionmaker[AsyncSession], owner_ids: tuple[UUID, ...]
) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            delete(OccurrenceRecord).where(OccurrenceRecord.owner_id.in_(owner_ids))
        )
        await session.execute(
            delete(PlanItemRecord).where(PlanItemRecord.owner_id.in_(owner_ids))
        )
        await session.execute(
            delete(PreferencesRecord).where(PreferencesRecord.owner_id.in_(owner_ids))
        )


@pytest.mark.asyncio
async def test_identity_roster_pages_persisted_profiles_without_writes(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owners = tuple(UUID(int=uuid4().int) for _ in range(7))
    async with sessions() as session, session.begin():
        session.add_all(_profile(owner_id) for owner_id in owners[:6])
    try:
        ordered = sorted(owners[:6])
        async with sessions() as session:
            before = await session.scalar(
                select(PreferencesRecord.version).where(PreferencesRecord.owner_id == ordered[0])
            )
            first = await list_review_profiles(session, limit=2)
            second = await list_review_profiles(
                session, after_owner_id=first.next_owner_id, limit=2
            )
            third = await list_review_profiles(
                session, after_owner_id=second.next_owner_id, limit=2
            )
            assert before == 4
        assert isinstance(first, PreferencesPage)
        assert tuple(item.owner_id for item in first.items) == tuple(ordered[:2])
        assert tuple(item.owner_id for item in second.items) == tuple(ordered[2:4])
        assert tuple(item.owner_id for item in third.items) == tuple(ordered[4:6])
        assert first.next_owner_id == ordered[1]
        assert second.next_owner_id == ordered[3]
        assert third.next_owner_id is None
        assert all(item.version == 4 and item.home_zone == "UTC" for item in first.items)
        async with sessions() as session:
            after = await session.scalar(
                select(PreferencesRecord.version).where(PreferencesRecord.owner_id == ordered[0])
            )
        assert after == before
        with pytest.raises(ValueError):
            await list_review_profiles(session, limit=True)
    finally:
        await _cleanup(sessions, owners)


async def _plan(
    session: AsyncSession,
    *,
    owner_id: UUID,
    occurrence_id: UUID,
    kind: str,
    title: str,
    local_date: date,
    state: str = "open",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    deadline_at: datetime | None = None,
    local_time: time | None = None,
) -> None:
    plan_item_id = uuid4()
    session.add(
        PlanItemRecord(id=plan_item_id, owner_id=owner_id, kind=kind, title=title, version=2)
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=plan_item_id,
            kind=kind,
            event_zone="Europe/Paris",
            local_date=local_date,
            local_time=local_time,
            fold=None,
            end_local_date=None,
            end_local_time=None,
            end_fold=None,
            starts_at=starts_at,
            ends_at=ends_at,
            deadline_at=deadline_at,
            duration_seconds=3600 if kind == "appointment" else None,
            state=state,
            version=3,
        )
    )


@pytest.mark.asyncio
async def test_planning_reads_preserve_saved_schedule_and_page_profile_local_untimed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, other_owner = uuid4(), uuid4()
    local_day = date(2026, 1, 1)
    at = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)  # Dec 31 in Los Angeles.
    untimed_ids = sorted((uuid4(), uuid4(), uuid4()))
    appointment_id, deadline_id, completed_id, cancelled_id = (uuid4() for _ in range(4))
    async with sessions() as session, session.begin():
        for index, occurrence_id in enumerate(untimed_ids):
            await _plan(
                session,
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                kind="untimed",
                title=f"Untimed {index}",
                local_date=date(2025, 12, 31),
            )
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=uuid4(),
            kind="untimed",
            title="Next local day",
            local_date=local_day,
        )
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=appointment_id,
            kind="appointment",
            title="Appointment",
            local_date=date(2026, 1, 1),
            local_time=time(11),
            starts_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
            ends_at=datetime(2026, 1, 1, 11, tzinfo=UTC),
        )
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=deadline_id,
            kind="deadline",
            title="Deadline",
            local_date=date(2026, 1, 1),
            local_time=time(12),
            deadline_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        )
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=completed_id,
            kind="task",
            title="Completed",
            local_date=date(2026, 1, 1),
            state="completed",
            starts_at=datetime(2026, 1, 1, 13, tzinfo=UTC),
            local_time=time(13),
        )
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=cancelled_id,
            kind="task",
            title="Cancelled",
            local_date=date(2026, 1, 1),
            state="cancelled",
            starts_at=datetime(2026, 1, 1, 14, tzinfo=UTC),
            local_time=time(14),
        )
        await _plan(
            session,
            owner_id=other_owner,
            occurrence_id=uuid4(),
            kind="untimed",
            title="Foreign owner",
            local_date=date(2025, 12, 31),
        )
    try:
        async with sessions() as session:
            first = await list_today_open_untimed(
                session, owner_id=owner_id, profile_zone="America/Los_Angeles", at=at, limit=2
            )
            second = await list_today_open_untimed(
                session,
                owner_id=owner_id,
                profile_zone="America/Los_Angeles",
                at=at,
                after_occurrence_id=first.next_occurrence_id,
                limit=2,
            )
            appointment = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=appointment_id
            )
            deadline = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=deadline_id
            )
            completed = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=completed_id
            )
            cancelled = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=cancelled_id
            )
            hidden = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=untimed_ids[0]
            )
            foreign_id = await session.scalar(
                select(OccurrenceRecord.id).where(OccurrenceRecord.owner_id == other_owner)
            )
            assert foreign_id is not None
            foreign = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=foreign_id
            )
        assert tuple(item.occurrence_id for item in first.items) == tuple(untimed_ids[:2])
        assert first.next_occurrence_id == untimed_ids[1]
        assert tuple(item.occurrence_id for item in second.items) == (untimed_ids[2],)
        assert second.next_occurrence_id is None
        assert appointment is not None and appointment.occurrence_version == 3
        assert appointment.ends_at == datetime(2026, 1, 1, 11, tzinfo=UTC)
        assert appointment.deadline_at is None
        assert deadline is not None and deadline.ends_at is None
        assert deadline.deadline_at == datetime(2026, 1, 1, 12, tzinfo=UTC)
        assert completed is not None and completed.state == "completed"
        assert cancelled is not None and cancelled.state == "cancelled"
        assert hidden is not None and hidden.ends_at is None and hidden.deadline_at is None
        assert first.items[0].source_created_at == hidden.source_created_at
        assert foreign is None
        with pytest.raises(ValueError):
            await list_today_open_untimed(
                session, owner_id=owner_id, profile_zone="UTC", at=at, limit=True
            )
    finally:
        await _cleanup(sessions, (owner_id, other_owner))


@pytest.mark.asyncio
async def test_planning_for_update_blocks_writer_until_caller_releases_lock(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    occurrence_id = uuid4()
    async with sessions() as session, session.begin():
        await _plan(
            session,
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            kind="untimed",
            title="Locked truth",
            local_date=date(2026, 1, 1),
        )
    try:
        async with sessions() as reader, sessions() as writer:
            locked = await get_accountability_occurrence(
                reader, owner_id=owner_id, occurrence_id=occurrence_id, for_update=True
            )
            assert locked is not None and locked.state == "open"
            started = asyncio.Event()

            async def complete_occurrence() -> None:
                started.set()
                await writer.execute(
                    update(OccurrenceRecord)
                    .where(
                        OccurrenceRecord.owner_id == owner_id,
                        OccurrenceRecord.id == occurrence_id,
                    )
                    .values(state="completed", version=OccurrenceRecord.version + 1)
                )
                await writer.commit()

            writer_task = asyncio.create_task(complete_occurrence())
            await started.wait()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(writer_task), timeout=0.2)
            still_open = await reader.scalar(
                select(OccurrenceRecord.state).where(
                    OccurrenceRecord.owner_id == owner_id,
                    OccurrenceRecord.id == occurrence_id,
                )
            )
            assert still_open == "open"
            await reader.rollback()
            await writer_task
        async with sessions() as session:
            released = await get_accountability_occurrence(
                session, owner_id=owner_id, occurrence_id=occurrence_id
            )
        assert released is not None and released.state == "completed"
    finally:
        await _cleanup(sessions, (owner_id,))
