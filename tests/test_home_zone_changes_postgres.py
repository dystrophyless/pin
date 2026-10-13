from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.contexts.identity.api import Preferences
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.application.home_zone_changes import (
    HomeZoneChangeGrant,
    HomeZoneChangeProof,
    HomeZoneChangeRequest,
    confirm_home_zone_change,
    preview_home_zone_change,
)
from pin.contexts.planning.domain.plans import (
    OneOffPlanDraft,
    PlanKind,
    resolve_schedule,
)
from pin.contexts.planning.infrastructure.home_zone_change_store import (
    SqlAlchemyHomeZoneChangeStore,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.persistence import run_local_async
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_zone_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_HOME_ZONE_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_HOME_ZONE_DATABASE_URL to the assigned Issue 15 zone fixture")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX):
        pytest.fail("home-zone database is outside the assigned pin_issue15_zone_ prefix")
    if port != DATABASE_PORT:
        pytest.fail("home-zone database is not on the assigned port")
    if directory.replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail("home-zone database is outside the assigned disposable PostgreSQL cluster")
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


@pytest.fixture(scope="session")
def home_zone_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _database_url()
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class _Verifier:
    def __init__(self, expires_at: datetime) -> None:
        self.expires_at = expires_at
        self.calls = 0

    async def verify_and_consume(
        self,
        proof: HomeZoneChangeProof,
        *,
        owner_id: UUID,
        projection_sha256: str,
        at: datetime,
    ) -> HomeZoneChangeGrant:
        self.calls += 1
        return HomeZoneChangeGrant(
            owner_id,
            proof.preview_id,
            proof.preview_version,
            projection_sha256,
            self.expires_at,
        )


async def _seed_profile(session: AsyncSession, owner_id: UUID) -> None:
    session.add(
        PreferencesRecord(
            owner_id=owner_id,
            language="en",
            home_zone="UTC",
            reminder_calendar_days=1,
            reminder_elapsed_hours=1,
            quiet_start=time(22),
            quiet_end=time(7),
            review_time=time(20),
            version=1,
        )
    )


async def _seed_occurrence(
    session: AsyncSession,
    *,
    owner_id: UUID,
    at: datetime,
    local_day: date,
    zone: str,
    local_clock: time = time(12),
    approved_version: int | None = 1,
    with_reminder: bool = True,
    state: str = "open",
) -> UUID:
    draft = OneOffPlanDraft(
        kind=PlanKind.TASK,
        title="Future task",
        local_date=local_day,
        local_time=local_clock,
        event_zone=zone,
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    plan_id, occurrence_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(
            id=plan_id,
            owner_id=owner_id,
            kind="task",
            title=draft.title,
            version=1,
        )
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=plan_id,
            kind=schedule.kind.value,
            event_zone=schedule.event_zone,
            local_date=schedule.local_date,
            local_time=schedule.local_time,
            fold=schedule.fold,
            end_local_date=schedule.end_local_date,
            end_local_time=schedule.end_local_time,
            end_fold=schedule.end_fold,
            starts_at=schedule.starts_at,
            ends_at=schedule.ends_at,
            deadline_at=schedule.deadline_at,
            duration_seconds=schedule.duration_seconds,
            state=state,
            version=1,
            reminder_preferences_version=approved_version,
        )
    )
    if with_reminder:
        session.add(
            ReminderRuleRecord(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind="elapsed_seconds",
                offset_amount=3600,
                source="default",
                due_at=schedule.starts_at - timedelta(hours=1),
            )
        )
    await session.flush()
    return occurrence_id


async def _seed_untimed_occurrence(
    session: AsyncSession,
    *,
    owner_id: UUID,
    local_day: date,
    zone: str,
    state: str = "open",
) -> UUID:
    draft = OneOffPlanDraft(
        kind=PlanKind.UNTIMED,
        title="Date-only plan",
        local_date=local_day,
        event_zone=zone,
        reminders=(),
    )
    schedule = resolve_schedule(
        draft,
        default_zone="UTC",
        now=datetime.combine(local_day - timedelta(days=1), time(12), UTC),
    )
    plan_id, occurrence_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(
            id=plan_id,
            owner_id=owner_id,
            kind=PlanKind.UNTIMED.value,
            title=draft.title,
            version=1,
        )
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=plan_id,
            kind=schedule.kind.value,
            event_zone=schedule.event_zone,
            local_date=schedule.local_date,
            local_time=None,
            fold=None,
            end_local_date=None,
            end_local_time=None,
            end_fold=None,
            starts_at=None,
            ends_at=None,
            deadline_at=None,
            duration_seconds=None,
            state=state,
            version=1,
            reminder_preferences_version=1,
        )
    )
    await session.flush()
    return occurrence_id


async def _seed_series(
    session: AsyncSession,
    *,
    owner_id: UUID,
    at: datetime,
) -> tuple[UUID, UUID, UUID]:
    anchor = date(2027, 1, 5)
    plan_id, series_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(
            id=plan_id,
            owner_id=owner_id,
            kind="task",
            title="Daily series",
            version=1,
        )
    )
    await session.flush()
    session.add(
        SeriesRecord(
            id=series_id,
            owner_id=owner_id,
            plan_item_id=plan_id,
            version=1,
            state="active",
            anchor_date=anchor,
            anchor_sequence=0,
            next_sequence=2,
            total_count=4,
            end_date=None,
            rule={
                "frequency": "daily",
                "interval": 1,
                "weekdays": [],
                "monthly_day": None,
                "last_day": False,
                "count": 4,
                "end_date": None,
            },
            typed_draft={
                "kind": "task",
                "title": "Daily series",
                "start_date": anchor.isoformat(),
                "local_time": "12:00:00",
                "event_zone": None,
                "duration_seconds": 3600,
                "end_local_time": None,
                "end_local_date": None,
                "fold": None,
                "end_fold": None,
                "reminders": None,
                "effective_reminders": [
                    {"kind": "elapsed_seconds", "amount": 3600, "source": "default"}
                ],
                "default_zone": "UTC",
                "approved_preferences_version": 1,
                "quiet_zone": "UTC",
                "quiet_start": "22:00:00",
                "quiet_end": "07:00:00",
                "recurrence": {
                    "frequency": "daily",
                    "interval": 1,
                    "weekdays": [],
                    "monthly_day": None,
                    "last_day": False,
                    "count": 4,
                    "end_date": None,
                },
            },
        )
    )
    await session.flush()
    completed = await _seed_occurrence(
        session,
        owner_id=owner_id,
        at=at,
        local_day=anchor - timedelta(days=2),
        zone="UTC",
        state="completed",
    )
    # Use the series' plan item and a historical stable occurrence identity.
    completed_row = await session.get(OccurrenceRecord, completed)
    assert completed_row is not None
    completed_row.plan_item_id = plan_id
    future_day = anchor + timedelta(days=1)
    schedule = resolve_schedule(
        OneOffPlanDraft(
            kind=PlanKind.TASK,
            title="Daily series",
            local_date=future_day,
            local_time=time(12),
            event_zone="UTC",
            duration_seconds=3600,
        ),
        default_zone="UTC",
        now=at,
    )
    future_id = uuid4()
    session.add(
        OccurrenceRecord(
            id=future_id,
            owner_id=owner_id,
            plan_item_id=plan_id,
            kind="task",
            event_zone="UTC",
            local_date=future_day,
            local_time=time(12),
            starts_at=schedule.starts_at,
            duration_seconds=3600,
            state="open",
            version=1,
            reminder_preferences_version=1,
        )
    )
    await session.flush()
    session.add_all(
        [
            SeriesOccurrenceRecord(
                owner_id=owner_id,
                series_id=series_id,
                occurrence_id=completed,
                sequence=0,
                recurrence_key="r1:0",
            ),
            SeriesOccurrenceRecord(
                owner_id=owner_id,
                series_id=series_id,
                occurrence_id=future_id,
                sequence=1,
                recurrence_key="r1:1",
            ),
        ]
    )
    session.add(
        ReminderRuleRecord(
            owner_id=owner_id,
            occurrence_id=future_id,
            offset_kind="elapsed_seconds",
            offset_amount=3600,
            source="default",
            due_at=schedule.starts_at - timedelta(hours=1),
        )
    )
    await session.flush()
    return series_id, future_id, completed


def test_zone_preview_keep_and_explicit_reinterpret_are_atomic(home_zone_sessions) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                preserved = await _seed_occurrence(
                    session, owner_id=owner, at=at, local_day=date(2027, 1, 5), zone="Europe/Paris"
                )
                selected = await _seed_occurrence(
                    session, owner_id=owner, at=at, local_day=date(2027, 1, 6), zone="UTC"
                )
                historical = await _seed_occurrence(
                    session,
                    owner_id=owner,
                    at=at,
                    local_day=date(2027, 1, 2),
                    zone="Europe/Paris",
                    state="completed",
                )

        candidate = Preferences(
            "en",
            "America/New_York",
            reminder_calendar_days=1,
            reminder_elapsed_hours=1,
            quiet_start=time(21),
            quiet_end=time(8),
            review_time=time(20),
            version=2,
        )
        async with home_zone_sessions() as session:
            async with session.begin():
                identity = SqlAlchemyIdentityStore(session)
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate, (selected,)),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=identity,
                    clock=_Clock(at),
                )
                assert len(preview.projection.payload["occurrences"]) == 2
                after_preview = await session.get(OccurrenceRecord, selected)
                assert after_preview is not None and after_preview.event_zone == "UTC"
                assert after_preview.version == 1

        verifier = _Verifier(at + timedelta(hours=1))
        async with home_zone_sessions() as session:
            async with session.begin():
                result = await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, preview.preview_version, "bound-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=verifier,
                    clock=_Clock(at),
                )
                assert result["profile_version"] == 2

        async with home_zone_sessions() as session:
            saved = await session.get(PreferencesRecord, owner)
            assert (
                saved is not None and saved.home_zone == "America/New_York" and saved.version == 2
            )
            preserved_row = await session.get(OccurrenceRecord, preserved)
            selected_row = await session.get(OccurrenceRecord, selected)
            historical_row = await session.get(OccurrenceRecord, historical)
            assert preserved_row is not None and preserved_row.event_zone == "Europe/Paris"
            assert preserved_row.version == 2 and preserved_row.reminder_preferences_version == 2
            assert selected_row is not None and selected_row.event_zone == "America/New_York"
            assert selected_row.local_date == date(2027, 1, 6)
            assert selected_row.version == 2 and selected_row.reminder_preferences_version == 2
            assert historical_row is not None and historical_row.version == 1
            events = list(
                (
                    await session.scalars(select(OutboxEvent).where(OutboxEvent.owner_id == owner))
                ).all()
            )
            assert len(events) == 2
            assert {event.aggregate_id for event in events} == {preserved, selected}
            assert all(event.aggregate_version == 2 for event in events)
        assert verifier.calls == 1

    run_local_async(scenario)


def test_profile_and_plans_rollback_together_when_identity_save_rejects(home_zone_sessions) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                occurrence_id = await _seed_occurrence(
                    session, owner_id=owner, at=at, local_day=date(2027, 1, 5), zone="UTC"
                )
        candidate = Preferences("en", "America/New_York", version=2)
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )

        class _RejectingIdentity:
            def __init__(self, delegate: SqlAlchemyIdentityStore) -> None:
                self.delegate = delegate

            async def lock_preferences(self, owner_id: UUID):
                return await self.delegate.lock_preferences(owner_id)

            async def save_preferences(self, **values):
                await self.delegate.save_preferences(**values)
                return None

        async with home_zone_sessions() as session:
            async with session.begin():
                with pytest.raises(ValueError, match="profile changed"):
                    async with session.begin_nested():
                        await confirm_home_zone_change(
                            HomeZoneChangeProof(preview.preview_id, 1, "bound-token"),
                            authenticated_owner_id=owner,
                            store=SqlAlchemyHomeZoneChangeStore(session),
                            identity=_RejectingIdentity(SqlAlchemyIdentityStore(session)),
                            verifier=_Verifier(at + timedelta(hours=1)),
                            clock=_Clock(at),
                        )
        async with home_zone_sessions() as session:
            profile = await session.get(PreferencesRecord, owner)
            row = await session.get(OccurrenceRecord, occurrence_id)
            assert profile is not None and profile.home_zone == "UTC" and profile.version == 1
            assert row is not None and row.version == 1 and row.event_zone == "UTC"
            assert (
                await session.scalar(select(OutboxEvent.id).where(OutboxEvent.owner_id == owner))
                is None
            )

    run_local_async(scenario)


def test_selected_series_reanchors_no_history_and_invalidates_by_series_version(
    home_zone_sessions,
) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                series_id, future_id, completed_id = await _seed_series(
                    session, owner_id=owner, at=at
                )
        candidate = Preferences(
            "en",
            "America/New_York",
            quiet_start=time(10),
            quiet_end=time(12),
            version=2,
        )
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(
                        owner,
                        1,
                        candidate,
                        reinterpret_series_ids=(series_id,),
                    ),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
                series_preview = preview.projection.payload["series"][0]
                assert series_preview["after_default_zone"] == "America/New_York"
                occurrence_preview = preview.projection.payload["occurrences"][0]
                assert occurrence_preview["after"]["event_zone"] == "America/New_York"
                assert (
                    occurrence_preview["reminders"]
                    or occurrence_preview["omitted_reminders"]
                    or occurrence_preview["reminder_notices"]
                )

        async with home_zone_sessions() as session:
            async with session.begin():
                await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, 1, "series-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=_Verifier(at + timedelta(hours=1)),
                    clock=_Clock(at),
                )
        async with home_zone_sessions() as session:
            profile = await session.get(PreferencesRecord, owner)
            series = await session.get(SeriesRecord, series_id)
            future = await session.get(OccurrenceRecord, future_id)
            completed = await session.get(OccurrenceRecord, completed_id)
            assert profile is not None and profile.version == 2
            assert series is not None and series.version == 2
            assert series.anchor_date == date(2027, 1, 5)
            assert series.anchor_sequence == 0 and series.next_sequence == 2
            assert series.total_count == 4
            assert series.typed_draft["default_zone"] == "America/New_York"
            assert series.typed_draft["approved_preferences_version"] == 2
            assert future is not None and future.event_zone == "America/New_York"
            assert future.version == 2 and future.reminder_preferences_version == 2
            assert completed is not None and completed.state == "completed"
            assert completed.version == 1 and completed.event_zone == "UTC"
            events = list(
                (
                    await session.scalars(select(OutboxEvent).where(OutboxEvent.owner_id == owner))
                ).all()
            )
            assert any(
                event.event_type == "planning.series_approval_updated"
                and event.aggregate_id == series_id
                and event.aggregate_version == 2
                for event in events
            )
            assert any(
                event.event_type == "planning.plan_rescheduled"
                and event.aggregate_id == future_id
                and event.aggregate_version == 2
                for event in events
            )

    run_local_async(scenario)


def test_stale_target_and_foreign_owner_cannot_apply_a_saved_preview(home_zone_sessions) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner, other_owner = uuid4(), uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                await _seed_profile(session, other_owner)
                occurrence_id = await _seed_occurrence(
                    session, owner_id=owner, at=at, local_day=date(2027, 1, 5), zone="UTC"
                )
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, Preferences("en", "Asia/Tokyo", version=2)),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
        async with home_zone_sessions() as session:
            async with session.begin():
                row = await session.get(OccurrenceRecord, occurrence_id)
                assert row is not None
                row.version = 2
        async with home_zone_sessions() as session:
            async with session.begin():
                with pytest.raises(ValueError, match="projection is stale"):
                    async with session.begin_nested():
                        await confirm_home_zone_change(
                            HomeZoneChangeProof(preview.preview_id, 1, "stale-token"),
                            authenticated_owner_id=owner,
                            store=SqlAlchemyHomeZoneChangeStore(session),
                            identity=SqlAlchemyIdentityStore(session),
                            verifier=_Verifier(at + timedelta(hours=1)),
                            clock=_Clock(at),
                        )
        async with home_zone_sessions() as session:
            async with session.begin():
                with pytest.raises(ValueError, match="expired or stale"):
                    async with session.begin_nested():
                        await confirm_home_zone_change(
                            HomeZoneChangeProof(preview.preview_id, 1, "foreign-token"),
                            authenticated_owner_id=other_owner,
                            store=SqlAlchemyHomeZoneChangeStore(session),
                            identity=SqlAlchemyIdentityStore(session),
                            verifier=_Verifier(at + timedelta(hours=1)),
                            clock=_Clock(at),
                        )
        async with home_zone_sessions() as session:
            profile = await session.get(PreferencesRecord, owner)
            assert profile is not None and profile.home_zone == "UTC" and profile.version == 1

    run_local_async(scenario)


def test_global_keep_allows_empty_selection_and_preserves_schedules(home_zone_sessions) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                occurrence_id = await _seed_occurrence(
                    session,
                    owner_id=owner,
                    at=at,
                    local_day=date(2027, 1, 5),
                    zone="Europe/Paris",
                )
        candidate = Preferences("en", "Asia/Tokyo", version=2)
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
                assert preview.projection.payload["reinterpret_occurrence_ids"] == []
                before = preview.projection.payload["occurrences"][0]["before"]
                after = preview.projection.payload["occurrences"][0]["after"]
                assert before == after
        async with home_zone_sessions() as session:
            async with session.begin():
                await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, 1, "keep-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=_Verifier(at + timedelta(hours=1)),
                    clock=_Clock(at),
                )
        async with home_zone_sessions() as session:
            row = await session.get(OccurrenceRecord, occurrence_id)
            assert row is not None and row.event_zone == "Europe/Paris"
            assert row.local_date == date(2027, 1, 5) and row.local_time == time(12)
            assert row.reminder_preferences_version == 2

    run_local_async(scenario)


def test_reinterpret_rejects_nonexistent_daylight_saving_wall_time(home_zone_sessions) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                occurrence_id = await _seed_occurrence(
                    session,
                    owner_id=owner,
                    at=at,
                    local_day=date(2027, 3, 14),
                    local_clock=time(2, 30),
                    zone="UTC",
                )
        async with home_zone_sessions() as session:
            async with session.begin():
                with pytest.raises(ValueError, match="scheduled time"):
                    await preview_home_zone_change(
                        HomeZoneChangeRequest(
                            owner,
                            1,
                            Preferences("en", "America/New_York", version=2),
                            reinterpret_occurrence_ids=(occurrence_id,),
                        ),
                        store=SqlAlchemyHomeZoneChangeStore(session),
                        identity=SqlAlchemyIdentityStore(session),
                        clock=_Clock(at),
                    )
        async with home_zone_sessions() as session:
            row = await session.get(OccurrenceRecord, occurrence_id)
            profile = await session.get(PreferencesRecord, owner)
            assert row is not None and row.event_zone == "UTC" and row.version == 1
            assert profile is not None and profile.home_zone == "UTC" and profile.version == 1

    run_local_async(scenario)


def test_date_only_keep_and_explicit_reinterpret_preserve_dates_and_no_times(
    home_zone_sessions,
) -> None:
    # At this instant Los Angeles is still on Dec 31. Date-only scope is based
    # on the current profile zone, not UTC or an inferred event-zone provenance.
    at = datetime(2027, 1, 1, 2, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                profile = await session.get(PreferencesRecord, owner)
                assert profile is not None
                profile.home_zone = "America/Los_Angeles"
                selected = await _seed_untimed_occurrence(
                    session,
                    owner_id=owner,
                    local_day=date(2026, 12, 31),
                    zone="America/Los_Angeles",
                )
                preserved = await _seed_untimed_occurrence(
                    session,
                    owner_id=owner,
                    local_day=date(2027, 1, 1),
                    zone="Europe/Paris",
                )
                past_open = await _seed_untimed_occurrence(
                    session,
                    owner_id=owner,
                    local_day=date(2026, 12, 30),
                    zone="UTC",
                )
                completed = await _seed_untimed_occurrence(
                    session,
                    owner_id=owner,
                    local_day=date(2026, 12, 31),
                    zone="UTC",
                    state="completed",
                )

        candidate = Preferences("en", "America/New_York", version=2)
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate, (selected,)),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
                projected = {
                    UUID(str(item["occurrence_id"])): item
                    for item in preview.projection.payload["occurrences"]
                }
                assert set(projected) == {selected, preserved}
                before = projected[selected]["before"]
                after = projected[selected]["after"]
                assert before["event_zone"] == "America/Los_Angeles"
                assert after["event_zone"] == "America/New_York"
                assert after["local_date"] == "2026-12-31"
                assert after["local_time"] is None and after["starts_at"] is None
                assert after["deadline_at"] is None and projected[selected]["reminders"] == []
                assert projected[preserved]["before"] == projected[preserved]["after"]
                assert projected[selected]["unresolved_reminders"] is False

        async with home_zone_sessions() as session:
            async with session.begin():
                await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, 1, "date-only-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=_Verifier(at + timedelta(hours=1)),
                    clock=_Clock(at),
                )

        async with home_zone_sessions() as session:
            changed = await session.get(OccurrenceRecord, selected)
            kept = await session.get(OccurrenceRecord, preserved)
            past = await session.get(OccurrenceRecord, past_open)
            done = await session.get(OccurrenceRecord, completed)
            assert changed is not None and changed.version == 2
            assert changed.event_zone == "America/New_York"
            assert changed.local_date == date(2026, 12, 31)
            assert changed.local_time is None and changed.starts_at is None
            assert changed.deadline_at is None and changed.duration_seconds is None
            assert changed.reminder_preferences_version == 2
            assert kept is not None and kept.version == 2
            assert kept.event_zone == "Europe/Paris" and kept.local_date == date(2027, 1, 1)
            assert kept.local_time is None and kept.starts_at is None
            assert past is not None and past.version == 1 and past.local_date == date(2026, 12, 30)
            assert done is not None and done.version == 1 and done.state == "completed"

    run_local_async(scenario)


def test_future_date_only_series_can_be_explicitly_reinterpreted_without_history_rewrite(
    home_zone_sessions,
) -> None:
    at = datetime(2027, 1, 1, 2, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        anchor = date(2026, 12, 31)
        plan_id, series_id = uuid4(), uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                profile = await session.get(PreferencesRecord, owner)
                assert profile is not None
                profile.home_zone = "America/Los_Angeles"
                session.add(
                    PlanItemRecord(
                        id=plan_id,
                        owner_id=owner,
                        kind="untimed",
                        title="Date-only recurring plan",
                        version=1,
                    )
                )
                session.add(
                    SeriesRecord(
                        id=series_id,
                        owner_id=owner,
                        plan_item_id=plan_id,
                        version=4,
                        state="active",
                        anchor_date=anchor,
                        anchor_sequence=0,
                        next_sequence=0,
                        total_count=4,
                        end_date=None,
                        rule={
                            "frequency": "daily",
                            "interval": 1,
                            "weekdays": [],
                            "monthly_day": None,
                            "last_day": False,
                            "count": 4,
                            "end_date": None,
                        },
                        typed_draft={
                            "kind": "untimed",
                            "title": "Date-only recurring plan",
                            "start_date": anchor.isoformat(),
                            "local_time": None,
                            "event_zone": "America/Los_Angeles",
                            "duration_seconds": None,
                            "effective_reminders": [],
                            "default_zone": "America/Los_Angeles",
                            "approved_preferences_version": 1,
                            "quiet_zone": "UTC",
                            "quiet_start": "22:00:00",
                            "quiet_end": "07:00:00",
                        },
                    )
                )

        candidate = Preferences("en", "America/New_York", version=2)
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate, reinterpret_series_ids=(series_id,)),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
                value = preview.projection.payload["series"][0]
                assert value["series_id"] == str(series_id)
                assert value["next_local_date"] == anchor.isoformat()
                assert value["after_default_zone"] == "America/New_York"
                assert value["after_effective_reminders"] == []
                assert value["unresolved_reminders"] is False
                assert preview.projection.unresolved_reminder_targets == ()

        async with home_zone_sessions() as session:
            async with session.begin():
                await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, 1, "untimed-series-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=_Verifier(at + timedelta(hours=1)),
                    clock=_Clock(at),
                )
        async with home_zone_sessions() as session:
            record = await session.get(SeriesRecord, series_id)
            assert record is not None and record.version == 5
            assert record.anchor_date == anchor and record.next_sequence == 0
            assert record.total_count == 4 and record.end_date is None
            assert record.typed_draft["default_zone"] == "America/New_York"
            assert record.typed_draft["event_zone"] == "America/New_York"
            assert record.typed_draft["local_time"] is None
            assert record.typed_draft["effective_reminders"] == []

    run_local_async(scenario)


def test_stopped_series_with_future_open_occurrence_is_requalified_without_resuming(
    home_zone_sessions,
) -> None:
    at = datetime(2027, 1, 1, 10, tzinfo=UTC)

    async def scenario() -> None:
        owner = uuid4()
        async with home_zone_sessions() as session:
            async with session.begin():
                await _seed_profile(session, owner)
                series_id, future_id, completed_id = await _seed_series(
                    session, owner_id=owner, at=at
                )
                series = await session.get(SeriesRecord, series_id)
                assert series is not None
                series.state = "stopped"

        candidate = Preferences("en", "America/New_York", version=2)
        async with home_zone_sessions() as session:
            async with session.begin():
                preview = await preview_home_zone_change(
                    HomeZoneChangeRequest(owner, 1, candidate),
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    clock=_Clock(at),
                )
                projected_series = preview.projection.payload["series"]
                assert [value["series_id"] for value in projected_series] == [str(series_id)]
                assert any(
                    value["occurrence_id"] == str(future_id)
                    for value in preview.projection.payload["occurrences"]
                )

        async with home_zone_sessions() as session:
            async with session.begin():
                await confirm_home_zone_change(
                    HomeZoneChangeProof(preview.preview_id, 1, "stopped-series-token"),
                    authenticated_owner_id=owner,
                    store=SqlAlchemyHomeZoneChangeStore(session),
                    identity=SqlAlchemyIdentityStore(session),
                    verifier=_Verifier(at + timedelta(hours=1)),
                    clock=_Clock(at),
                )
        async with home_zone_sessions() as session:
            series = await session.get(SeriesRecord, series_id)
            future = await session.get(OccurrenceRecord, future_id)
            completed = await session.get(OccurrenceRecord, completed_id)
            assert series is not None and series.state == "stopped" and series.version == 2
            assert series.typed_draft["approved_preferences_version"] == 2
            assert future is not None and future.version == 2
            assert future.reminder_preferences_version == 2
            assert future.state == "open"
            assert completed is not None and completed.state == "completed"
            assert completed.version == 1
            events = list(
                (
                    await session.scalars(select(OutboxEvent).where(OutboxEvent.owner_id == owner))
                ).all()
            )
            assert any(
                event.event_type == "planning.series_approval_updated"
                and event.aggregate_id == series_id
                and event.aggregate_version == 2
                for event in events
            )

    run_local_async(scenario)
