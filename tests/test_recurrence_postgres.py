from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
    select,
    text,
    update,
)
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.engine import make_url

from pin.contexts.billing.api import SqlAlchemyAccessUsageStore
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceConfirmationGrant,
    RecurrenceConfirmationProof,
)
from pin.contexts.planning.application.recurrence_controls import (
    StopRecurrenceSeries,
    preview_stop_series,
    stop_confirmed_series,
)
from pin.contexts.planning.application.recurrence_exceptions import (
    ConfirmedRecurrenceException,
    apply_confirmed_recurrence_exception,
    preview_recurrence_exception,
)
from pin.contexts.planning.application.recurrence_generation import (
    GenerateRecurrenceSeries,
    generate_recurrence_horizon,
)
from pin.contexts.planning.application.recurrence_preview import (
    CreateConfirmedSeries,
    RecurrenceIdempotencyConflict,
    create_confirmed_series,
    preview_recurring_series,
    recurrence_defaults,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, ReminderRuleRecord
from pin.contexts.planning.infrastructure.series_models import (
    SeriesExceptionRecord,
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.contexts.planning.infrastructure.series_store import (
    SeriesNotFound,
    SeriesStopped,
    SeriesVersionConflict,
    SqlAlchemySeriesStore,
)
from pin.persistence import SqlAlchemyUnitOfWork, run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.models import ActionReceipt
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_RECURRENCE_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
TEST_SCHEMA = "planning_recurrence_test"
NOW = datetime(2026, 10, 14, 8, tzinfo=UTC)
test_metadata = MetaData()
confirmation_grants = Table(
    "recurrence_confirmation_grants",
    test_metadata,
    Column("credential", String(120), primary_key=True),
    Column("owner_id", String(36), nullable=False),
    Column("preview_id", String(36), nullable=False),
    Column("preview_version", Integer, nullable=False),
    Column("producer_id", String(120), nullable=False),
    Column("approved_projection_sha256", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("cancelled", Boolean, nullable=False, default=False),
    Column("consumed", Boolean, nullable=False, default=False),
    schema=TEST_SCHEMA,
)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_RECURRENCE_DATABASE_URL to the fresh owned issue-10 PostgreSQL database",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def recurrence_database() -> Iterator[str]:
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.username) == ("127.0.0.1", 51017, "pin_test")
    assert url.database and url.database.startswith("pin_issue10_")
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            assert directory.rstrip("/").casefold() == DATA_DIRECTORY.casefold()
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    yield DATABASE_URL


@pytest.fixture(scope="session")
def recurrence_sessions(recurrence_database: str):
    engine = create_engine(recurrence_database)
    sessions = create_session_factory(engine)
    sync_engine = create_sync_engine(make_url(recurrence_database))
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"'))
    test_metadata.create_all(sync_engine)
    yield sessions
    with sync_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
    sync_engine.dispose()
    run_local_async(engine.dispose)


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class PersistedRecurrenceProducer:
    """Test-only trusted proof producer; grant consumption shares the command UoW."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions

    async def issue_create(
        self,
        owner_id: UUID,
        draft: RecurringPlanDraft,
        *,
        now: datetime = NOW,
        lifetime: timedelta = timedelta(days=5),
    ) -> RecurrenceConfirmationProof:
        proof, credential = _proof()
        async with self.sessions() as session, session.begin():
            preferences = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
            assert preferences is not None
            preview = preview_recurring_series(
                owner_id,
                draft,
                expected_preferences_version=preferences.version,
                default_zone=preferences.home_zone,
                default_reminders=recurrence_defaults(preferences),
                at=now,
            )
            await self._insert(
                session,
                proof,
                credential,
                owner_id,
                preview.approval_projection_sha256,
                now,
                now + lifetime,
            )
        return proof

    async def issue_exception(
        self,
        owner_id: UUID,
        series_id: UUID,
        change: RecurrenceExceptionDraft,
        *,
        now: datetime = NOW,
        lifetime: timedelta = timedelta(days=5),
    ) -> RecurrenceConfirmationProof:
        proof, credential = _proof()
        async with self.sessions() as session, session.begin():
            preferences = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
            snapshot = await SqlAlchemySeriesStore(session).get_series_snapshot(owner_id, series_id)
            assert preferences is not None and snapshot is not None
            preview = preview_recurrence_exception(
                snapshot,
                change,
                owner_id=owner_id,
                at=now,
                through=change.replacement.local_date + timedelta(days=366),
                default_zone=preferences.home_zone,
            )
            await self._insert(
                session,
                proof,
                credential,
                owner_id,
                preview.approval_projection_sha256,
                now,
                now + lifetime,
            )
        return proof

    async def issue_stop(
        self, owner_id: UUID, series_id: UUID, *, now: datetime = NOW
    ) -> RecurrenceConfirmationProof:
        proof, credential = _proof()
        async with self.sessions() as session, session.begin():
            snapshot = await SqlAlchemySeriesStore(session).get_series_snapshot(owner_id, series_id)
            assert snapshot is not None
            preview = preview_stop_series(snapshot)
            await self._insert(
                session,
                proof,
                credential,
                owner_id,
                preview.approval_projection_sha256,
                now,
                now + timedelta(days=5),
            )
        return proof

    def for_session(self, session):
        return PersistedRecurrenceVerifier(session)

    async def state(self, proof: RecurrenceConfirmationProof) -> tuple[bool, bool]:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(confirmation_grants.c.cancelled, confirmation_grants.c.consumed).where(
                        confirmation_grants.c.credential == proof.credential
                    )
                )
            ).one()
            return row.cancelled, row.consumed

    async def _insert(
        self, session, proof, credential, owner_id, digest, issued_at, expires_at
    ) -> None:
        await session.execute(
            confirmation_grants.insert().values(
                credential=credential,
                owner_id=str(owner_id),
                preview_id=str(proof.preview_id),
                preview_version=proof.preview_version,
                producer_id="trusted-test-recurrence-producer",
                approved_projection_sha256=digest,
                issued_at=issued_at,
                expires_at=expires_at,
                cancelled=False,
                consumed=False,
            )
        )


class PersistedRecurrenceVerifier:
    def __init__(self, session) -> None:
        self.session = session

    async def verify_and_consume(
        self,
        proof: RecurrenceConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> RecurrenceConfirmationGrant | None:
        row = (
            (
                await self.session.execute(
                    select(confirmation_grants)
                    .where(confirmation_grants.c.credential == proof.credential)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row.cancelled or row.consumed:
            return None
        if (
            row.owner_id != str(owner_id)
            or row.preview_id != str(proof.preview_id)
            or row.preview_version != proof.preview_version
            or row.producer_id != "trusted-test-recurrence-producer"
            or row.approved_projection_sha256 != approved_projection_sha256
            or row.issued_at > at
            or row.expires_at <= at
        ):
            return None
        await self.session.execute(
            update(confirmation_grants)
            .where(confirmation_grants.c.credential == proof.credential)
            .values(consumed=True)
        )
        await self.session.flush()
        return RecurrenceConfirmationGrant(
            owner_id,
            proof.preview_id,
            proof.preview_version,
            row.producer_id,
            row.approved_projection_sha256,
            row.issued_at,
            row.expires_at,
        )


class PausingRecurrenceVerifier:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def verify_and_consume(self, *args, **kwargs):
        self.entered.set()
        await self.release.wait()
        return await self.inner.verify_and_consume(*args, **kwargs)


def _proof() -> tuple[RecurrenceConfirmationProof, str]:
    credential = f"trusted-recurrence-test:{uuid4().hex}"
    return RecurrenceConfirmationProof(uuid4(), 2, credential), credential


def _profile(owner_id: UUID) -> PreferencesRecord:
    return PreferencesRecord(
        owner_id=owner_id,
        language="en",
        home_zone="UTC",
        reminder_calendar_days=0,
        reminder_elapsed_hours=0,
        quiet_start=time(22),
        quiet_end=time(8),
        review_time=time(20),
        version=1,
    )


async def _seed_owner(sessions, owner_id: UUID) -> None:
    async with sessions() as session, session.begin():
        session.add(_profile(owner_id))
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"trial-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="trial-v1",
                starts_at=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=8),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )


def _daily_draft(start: date, *, count: int | None = 80) -> RecurringPlanDraft:
    return RecurringPlanDraft(
        PlanKind.TASK,
        "Daily review",
        start,
        time(10),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.DAILY, count=count),
    )


def _weekly_draft(start: date, *, count: int | None = 10) -> RecurringPlanDraft:
    return RecurringPlanDraft(
        PlanKind.TASK,
        "Class",
        start,
        time(9),
        "UTC",
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(start.weekday(),), count=count),
    )


def _next_weekday(anchor: date, weekday: int) -> date:
    delta = (weekday - anchor.weekday()) % 7
    return anchor + timedelta(days=delta or 7)


async def _create(
    sessions,
    owner_id: UUID,
    draft: RecurringPlanDraft,
    proof: RecurrenceConfirmationProof,
    *,
    action_key: str,
    verifier: PersistedRecurrenceProducer,
    store=None,
    clock=None,
) -> dict[str, object]:
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        session = uow.session
        return await create_confirmed_series(
            CreateConfirmedSeries(action_key, 1, draft, proof),
            authenticated_owner_id=owner_id,
            store=store or SqlAlchemySeriesStore(session),
            identity=SqlAlchemyIdentityStore(session),
            billing=SqlAlchemyAccessUsageStore(session),
            confirmation_verifier=verifier.for_session(session),
            clock=clock or FixedClock(NOW),
        )


def test_postgres_concurrent_create_replays_one_series_and_receipt(recurrence_sessions) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        draft = _daily_draft(NOW.date() + timedelta(days=1), count=5)
        proof = await producer.issue_create(owner_id, draft)
        results = await asyncio.gather(
            _create(
                recurrence_sessions,
                owner_id,
                draft,
                proof,
                action_key="issue10-create-replay",
                verifier=producer,
            ),
            _create(
                recurrence_sessions,
                owner_id,
                draft,
                proof,
                action_key="issue10-create-replay",
                verifier=producer,
            ),
        )
        assert results[0] == results[1]
        series_id = UUID(results[0]["series_id"])
        async with recurrence_sessions() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(SeriesRecord)
                .where(SeriesRecord.id == series_id, SeriesRecord.owner_id == owner_id)
            ) == 1
            assert await session.scalar(
                select(func.count())
                .select_from(SeriesOccurrenceRecord)
                .where(SeriesOccurrenceRecord.series_id == series_id)
            ) == 5
            assert await session.scalar(
                select(func.count())
                .select_from(ActionReceipt)
                .where(
                    ActionReceipt.owner_id == owner_id,
                    ActionReceipt.action_key == "issue10-create-replay",
                )
            ) == 1
            assert await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.owner_id == owner_id)
            ) == 6
        assert await producer.state(proof) == (False, True)

    run_local_async(scenario)


def test_postgres_generation_resumes_after_restart_and_serializes_concurrent_workers(
    recurrence_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        draft = _daily_draft(NOW.date() + timedelta(days=1), count=80)
        proof = await producer.issue_create(owner_id, draft)
        result = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            proof,
            action_key="issue10-restart-create",
            verifier=producer,
        )
        series_id = UUID(result["series_id"])
        assert len(result["occurrences"]) == 32

        async def generate() -> dict[str, object]:
            async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
                return await generate_recurrence_horizon(
                    GenerateRecurrenceSeries(
                        series_id,
                        1,
                        NOW.date() + timedelta(days=100),
                    ),
                    authenticated_owner_id=owner_id,
                    store=SqlAlchemySeriesStore(uow.session),
                    clock=FixedClock(NOW),
                )

        outcomes = await asyncio.gather(generate(), generate(), return_exceptions=True)
        assert sum(isinstance(item, dict) for item in outcomes) == 1
        assert sum(isinstance(item, SeriesVersionConflict) for item in outcomes) == 1

        # A newly constructed store resumes from its durable cursor after the
        # process has restarted; an identical covered horizon adds no rows.
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            snapshot = await SqlAlchemySeriesStore(uow.session).get_series_snapshot(
                owner_id, series_id
            )
            assert snapshot is not None and snapshot.version == 2
            replay = await generate_recurrence_horizon(
                GenerateRecurrenceSeries(
                    series_id,
                    snapshot.version,
                    NOW.date() + timedelta(days=100),
                ),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                clock=FixedClock(NOW),
            )
            assert replay["occurrences"] == []
        async with recurrence_sessions() as session:
            records = list(
                (
                    await session.scalars(
                        select(SeriesOccurrenceRecord)
                        .where(SeriesOccurrenceRecord.series_id == series_id)
                        .order_by(SeriesOccurrenceRecord.sequence)
                    )
                ).all()
            )
            assert len(records) == 80
            assert [row.sequence for row in records] == list(range(80))
            assert len({row.recurrence_key for row in records}) == 80

    run_local_async(scenario)


def test_postgres_unbounded_series_extends_across_generation_restart(recurrence_sessions) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        draft = _daily_draft(NOW.date() + timedelta(days=1), count=None)
        proof = await producer.issue_create(owner_id, draft)
        result = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            proof,
            action_key="issue10-unbounded-create",
            verifier=producer,
        )
        series_id = UUID(result["series_id"])
        assert len(result["occurrences"]) == 32

        async with recurrence_sessions() as session:
            series = await session.get(SeriesRecord, series_id)
            assert series is not None and series.total_count is None and series.end_date is None

        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            first_extension = await generate_recurrence_horizon(
                GenerateRecurrenceSeries(series_id, 1, NOW.date() + timedelta(days=100)),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                clock=FixedClock(NOW),
            )
        assert first_extension["next_sequence"] == 100

        # A newly constructed store resumes the unbounded series at its durable
        # cursor and extends it without assigning a synthetic final count.
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            snapshot = await SqlAlchemySeriesStore(uow.session).get_series_snapshot(
                owner_id, series_id
            )
            assert snapshot is not None and snapshot.total_count is None
            second_extension = await generate_recurrence_horizon(
                GenerateRecurrenceSeries(
                    series_id, snapshot.version, NOW.date() + timedelta(days=150)
                ),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                clock=FixedClock(NOW),
            )
        assert second_extension["next_sequence"] == 150
        async with recurrence_sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(SeriesOccurrenceRecord)
                        .where(SeriesOccurrenceRecord.series_id == series_id)
                        .order_by(SeriesOccurrenceRecord.sequence)
                    )
                ).all()
            )
            assert len(rows) == 150
            assert [row.sequence for row in rows] == list(range(150))
            assert len({row.recurrence_key for row in rows}) == 150
            series = await session.get(SeriesRecord, series_id)
            assert series is not None and series.total_count is None and series.end_date is None

    run_local_async(scenario)


def test_postgres_this_and_future_reanchor_preserves_ids_count_and_terminal_history(
    recurrence_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        start = _next_weekday(NOW.date(), 0)
        draft = _weekly_draft(start, count=10)
        proof = await producer.issue_create(owner_id, draft)
        result = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            proof,
            action_key="issue10-reanchor-create",
            verifier=producer,
        )
        series_id = UUID(result["series_id"])
        before = {
            int(row["sequence"]): UUID(row["occurrence_id"])
            for row in result["occurrences"]
        }
        async with recurrence_sessions() as session, session.begin():
            await session.execute(
                update(OccurrenceRecord)
                .where(OccurrenceRecord.id == before[0], OccurrenceRecord.owner_id == owner_id)
                .values(state="completed", version=2)
            )

        sequence = 4
        new_date = start + timedelta(days=sequence * 7 + 2)
        change = RecurrenceExceptionDraft(
            RecurrenceExceptionScope.THIS_AND_FUTURE,
            sequence,
            OneOffPlanDraft(PlanKind.TASK, "Class", new_date, time(9), "UTC"),
            RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,), count=10),
        )
        command_id = "issue10-reanchor-apply"
        exception_proof = await producer.issue_exception(owner_id, series_id, change)
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            response = await apply_confirmed_recurrence_exception(
                ConfirmedRecurrenceException(command_id, series_id, 1, 1, change, exception_proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                identity=SqlAlchemyIdentityStore(uow.session),
                billing=SqlAlchemyAccessUsageStore(uow.session),
                confirmation_verifier=producer.for_session(uow.session),
                clock=FixedClock(NOW),
            )
        assert response["remaining_count"] == 6
        assert len(response["occurrences"]) == 6
        assert await producer.state(exception_proof) == (False, True)
        async with recurrence_sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(SeriesOccurrenceRecord, OccurrenceRecord)
                        .join(
                            OccurrenceRecord,
                            OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id,
                        )
                        .where(SeriesOccurrenceRecord.series_id == series_id)
                        .order_by(SeriesOccurrenceRecord.sequence)
                    )
                ).all()
            )
            assert len(rows) == 10
            for association, occurrence in rows:
                assert before[association.sequence] == occurrence.id
                if association.sequence < sequence:
                    assert occurrence.local_date == start + timedelta(
                        days=association.sequence * 7
                    )
                else:
                    assert occurrence.local_date == new_date + timedelta(
                        days=(association.sequence - sequence) * 7
                    )
            terminal = await session.get(OccurrenceRecord, before[0])
            assert terminal is not None and terminal.state == "completed" and terminal.version == 2
            series = await session.get(SeriesRecord, series_id)
            assert series is not None
            assert (series.total_count, series.anchor_sequence, series.anchor_date) == (
                10,
                sequence,
                new_date,
            )
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.aggregate_id == before[sequence],
                            OutboxEvent.owner_id == owner_id,
                        )
                    )
                ).all()
            )
            assert {(event.event_type, event.aggregate_version) for event in events} >= {
                ("planning.series_occurrence_created", 1),
                ("planning.series_occurrence_rescheduled", 2),
            }
            assert await session.scalar(
                select(func.count())
                .select_from(SeriesExceptionRecord)
                .where(SeriesExceptionRecord.series_id == series_id)
            ) == 1

    run_local_async(scenario)


def test_postgres_exception_replay_conflicts_when_only_reminder_fields_change(
    recurrence_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        start = _next_weekday(NOW.date(), 0)
        draft = _weekly_draft(start, count=4)
        create_proof = await producer.issue_create(owner_id, draft)
        created = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            create_proof,
            action_key="issue10-reminder-replay-create",
            verifier=producer,
        )
        series_id = UUID(created["series_id"])
        occurrence_id = UUID(created["occurrences"][1]["occurrence_id"])
        original_reminder = ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "explicit")
        change = RecurrenceExceptionDraft(
            RecurrenceExceptionScope.SINGLE,
            1,
            OneOffPlanDraft(
                PlanKind.TASK,
                "Class",
                start + timedelta(days=8),
                time(9),
                "UTC",
                reminders=(original_reminder,),
            ),
        )
        proof = await producer.issue_exception(owner_id, series_id, change)
        action_key = "issue10-reminder-replay"
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            await apply_confirmed_recurrence_exception(
                ConfirmedRecurrenceException(action_key, series_id, 1, 1, change, proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                identity=SqlAlchemyIdentityStore(uow.session),
                billing=SqlAlchemyAccessUsageStore(uow.session),
                confirmation_verifier=producer.for_session(uow.session),
                clock=FixedClock(NOW),
            )

        async with recurrence_sessions() as session:
            before_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.owner_id == owner_id)
            )
            series = await session.get(SeriesRecord, series_id)
            assert series is not None and series.version == 2
            reminders = list(
                (
                    await session.scalars(
                        select(ReminderRuleRecord).where(
                            ReminderRuleRecord.occurrence_id == occurrence_id
                        )
                    )
                ).all()
            )
            assert len(reminders) == 1 and reminders[0].offset_amount == 3600

        changed_reminder = ReminderOffset(OffsetKind.ELAPSED_SECONDS, 7200, "explicit")
        conflicting_change = RecurrenceExceptionDraft(
            RecurrenceExceptionScope.SINGLE,
            1,
            OneOffPlanDraft(
                PlanKind.TASK,
                "Class",
                start + timedelta(days=8),
                time(9),
                "UTC",
                reminders=(changed_reminder,),
            ),
        )
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            with pytest.raises(RecurrenceIdempotencyConflict):
                await apply_confirmed_recurrence_exception(
                    ConfirmedRecurrenceException(
                        action_key, series_id, 1, 1, conflicting_change, proof
                    ),
                    authenticated_owner_id=owner_id,
                    store=SqlAlchemySeriesStore(uow.session),
                    identity=SqlAlchemyIdentityStore(uow.session),
                    billing=SqlAlchemyAccessUsageStore(uow.session),
                    confirmation_verifier=producer.for_session(uow.session),
                    clock=FixedClock(NOW),
                )

        async with recurrence_sessions() as session:
            after_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.owner_id == owner_id)
            )
            series = await session.get(SeriesRecord, series_id)
            assert series is not None and series.version == 2
            reminders = list(
                (
                    await session.scalars(
                        select(ReminderRuleRecord).where(
                            ReminderRuleRecord.occurrence_id == occurrence_id
                        )
                    )
                ).all()
            )
            assert len(reminders) == 1 and reminders[0].offset_amount == 3600
            assert after_events == before_events
            assert await session.scalar(
                select(func.count())
                .select_from(SeriesExceptionRecord)
                .where(SeriesExceptionRecord.series_id == series_id)
            ) == 1

    run_local_async(scenario)


@pytest.mark.parametrize(
    ("changed_state", "changed_version"),
    [("completed", 2), ("cancelled", 2), ("open", 2)],
)
def test_postgres_exception_rejects_child_occurrence_changed_during_proof(
    recurrence_sessions, changed_state: str, changed_version: int
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        start = _next_weekday(NOW.date(), 0)
        draft = _weekly_draft(start, count=5)
        create_proof = await producer.issue_create(owner_id, draft)
        created = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            create_proof,
            action_key="issue10-child-race-create",
            verifier=producer,
        )
        series_id = UUID(created["series_id"])
        target_sequence = 1
        target_id = UUID(created["occurrences"][target_sequence]["occurrence_id"])
        change = RecurrenceExceptionDraft(
            RecurrenceExceptionScope.THIS_AND_FUTURE,
            target_sequence,
            OneOffPlanDraft(
                PlanKind.TASK,
                "Class",
                start + timedelta(days=9),
                time(9),
                "UTC",
            ),
            RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,), count=5),
        )
        proof = await producer.issue_exception(owner_id, series_id, change)
        pausing = PausingRecurrenceVerifier(None)
        action_key = "issue10-child-race-apply"

        async def apply() -> dict[str, object]:
            async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
                pausing.inner = producer.for_session(uow.session)
                return await apply_confirmed_recurrence_exception(
                    ConfirmedRecurrenceException(
                        action_key, series_id, 1, 1, change, proof
                    ),
                    authenticated_owner_id=owner_id,
                    store=SqlAlchemySeriesStore(uow.session),
                    identity=SqlAlchemyIdentityStore(uow.session),
                    billing=SqlAlchemyAccessUsageStore(uow.session),
                    confirmation_verifier=pausing,
                    clock=FixedClock(NOW),
                )

        async with recurrence_sessions() as session:
            initial_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.owner_id == owner_id)
            )

        command_task = asyncio.create_task(apply())
        await asyncio.wait_for(pausing.entered.wait(), timeout=5)
        async with recurrence_sessions() as session, session.begin():
            await session.execute(
                update(OccurrenceRecord)
                .where(
                    OccurrenceRecord.id == target_id,
                    OccurrenceRecord.owner_id == owner_id,
                )
                .values(state=changed_state, version=changed_version)
            )
        pausing.release.set()
        with pytest.raises(SeriesVersionConflict):
            await command_task
        assert await producer.state(proof) == (False, False)

        async with recurrence_sessions() as session:
            target = await session.get(OccurrenceRecord, target_id)
            series = await session.get(SeriesRecord, series_id)
            assert target is not None
            assert target.state == changed_state and target.version == changed_version
            assert series is not None and series.version == 1
            assert await session.scalar(
                select(func.count())
                .select_from(SeriesExceptionRecord)
                .where(SeriesExceptionRecord.series_id == series_id)
            ) == 0
            assert await session.scalar(
                select(func.count())
                .select_from(ActionReceipt)
                .where(
                    ActionReceipt.owner_id == owner_id,
                    ActionReceipt.action_key == action_key,
                )
            ) == 0
            after_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.owner_id == owner_id)
            )
            assert after_events == initial_events
            others = list(
                (
                    await session.execute(
                        select(SeriesOccurrenceRecord, OccurrenceRecord)
                        .join(
                            OccurrenceRecord,
                            OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id,
                        )
                        .where(SeriesOccurrenceRecord.series_id == series_id)
                        .order_by(SeriesOccurrenceRecord.sequence)
                    )
                ).all()
            )
            for association, occurrence in others:
                assert occurrence.id == UUID(
                    created["occurrences"][association.sequence]["occurrence_id"]
                )
                if association.sequence != target_sequence:
                    assert occurrence.version == 1
                    assert occurrence.state == "open"

    run_local_async(scenario)


def test_postgres_unbounded_this_and_future_reanchors_then_generation_continues(
    recurrence_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        start = _next_weekday(NOW.date(), 0)
        draft = _weekly_draft(start, count=None)
        proof = await producer.issue_create(owner_id, draft)
        created = await _create(
            recurrence_sessions,
            owner_id,
            draft,
            proof,
            action_key="issue10-unbounded-reanchor-create",
            verifier=producer,
        )
        series_id = UUID(created["series_id"])
        initial = {
            int(row["sequence"]): UUID(row["occurrence_id"])
            for row in created["occurrences"]
        }
        sequence = 4
        new_date = start + timedelta(days=sequence * 7 + 2)
        reminder = ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "explicit")
        change = RecurrenceExceptionDraft(
            RecurrenceExceptionScope.THIS_AND_FUTURE,
            sequence,
            OneOffPlanDraft(
                PlanKind.TASK,
                "Class",
                new_date,
                time(9),
                "UTC",
                reminders=(reminder,),
            ),
            RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(2,)),
        )
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            store = SqlAlchemySeriesStore(uow.session)
            snapshot = await store.get_series_snapshot(owner_id, series_id)
            assert snapshot is not None
            preview = preview_recurrence_exception(
                snapshot,
                change,
                owner_id=owner_id,
                at=NOW,
                through=new_date + timedelta(days=366),
            )
        assert preview.updated_draft.reminders == (reminder,)
        assert preview.projection.occurrences
        assert all(
            tuple(offset for offset, _ in occurrence.reminders) == (reminder,)
            for occurrence in preview.projection.occurrences
        )
        exception_proof = await producer.issue_exception(owner_id, series_id, change)
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            response = await apply_confirmed_recurrence_exception(
                ConfirmedRecurrenceException(
                    "issue10-unbounded-reanchor-apply", series_id, 1, 1, change, exception_proof
                ),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                identity=SqlAlchemyIdentityStore(uow.session),
                billing=SqlAlchemyAccessUsageStore(uow.session),
                confirmation_verifier=producer.for_session(uow.session),
                clock=FixedClock(NOW),
            )
        assert response["remaining_count"] is None
        async with recurrence_sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(SeriesOccurrenceRecord, OccurrenceRecord)
                        .join(
                            OccurrenceRecord,
                            OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id,
                        )
                        .where(SeriesOccurrenceRecord.series_id == series_id)
                        .order_by(SeriesOccurrenceRecord.sequence)
                    )
                ).all()
            )
            assert len(rows) == len(initial)
            for association, occurrence in rows:
                assert occurrence.id == initial[association.sequence]
                if association.sequence < sequence:
                    assert occurrence.local_date == start + timedelta(
                        days=association.sequence * 7
                    )
                else:
                    assert occurrence.local_date == new_date + timedelta(
                        days=(association.sequence - sequence) * 7
                    )
            series = await session.get(SeriesRecord, series_id)
            assert series is not None
            assert series.total_count is None and series.end_date is None
            assert (series.anchor_sequence, series.anchor_date) == (sequence, new_date)
            expected_reminder_payload = [
                {"kind": reminder.kind.value, "amount": reminder.amount, "source": reminder.source}
            ]
            assert series.typed_draft["reminders"] == expected_reminder_payload
            assert series.typed_draft["effective_reminders"] == expected_reminder_payload
            for association, occurrence in rows:
                occurrence_reminders = list(
                    (
                        await session.scalars(
                            select(ReminderRuleRecord).where(
                                ReminderRuleRecord.occurrence_id == occurrence.id
                            )
                        )
                    ).all()
                )
                if association.sequence < sequence:
                    assert occurrence_reminders == []
                else:
                    assert len(occurrence_reminders) == 1
                    assert occurrence_reminders[0].offset_amount == reminder.amount
                    assert occurrence_reminders[0].offset_kind == reminder.kind.value

        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            snapshot = await SqlAlchemySeriesStore(uow.session).get_series_snapshot(
                owner_id, series_id
            )
            assert snapshot is not None
            extended = await generate_recurrence_horizon(
                GenerateRecurrenceSeries(
                    series_id, snapshot.version, new_date + timedelta(days=180)
                ),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                clock=FixedClock(NOW),
            )
        assert extended["occurrences"]
        assert extended["next_sequence"] > max(initial)
        async with recurrence_sessions() as session:
            series = await session.get(SeriesRecord, series_id)
            assert series is not None and series.total_count is None
            generated_ids = [UUID(item["occurrence_id"]) for item in extended["occurrences"]]
            generated_reminders = list(
                (
                    await session.scalars(
                        select(ReminderRuleRecord).where(
                            ReminderRuleRecord.occurrence_id.in_(generated_ids)
                        )
                    )
                ).all()
            )
            assert len(generated_reminders) == len(generated_ids)
            assert all(
                item.offset_amount == reminder.amount
                and item.offset_kind == reminder.kind.value
                and item.source == reminder.source
                for item in generated_reminders
            )

    run_local_async(scenario)


def test_postgres_wrong_owner_expired_access_stops_series_and_preserves_occurrences(
    recurrence_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(recurrence_sessions, owner_id)
        producer = PersistedRecurrenceProducer(recurrence_sessions)
        fall_draft = RecurringPlanDraft(
            PlanKind.TASK,
            "Sunday session",
            date(2026, 10, 25),
            time(1, 30),
            "America/New_York",
            RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(6,), count=2),
        )
        proof = await producer.issue_create(owner_id, fall_draft)
        result = await _create(
            recurrence_sessions,
            owner_id,
            fall_draft,
            proof,
            action_key="issue10-dst-fall",
            verifier=producer,
        )
        series_id = UUID(result["series_id"])
        async with recurrence_sessions() as session:
            repeated = await session.scalar(
                select(OccurrenceRecord).where(
                    OccurrenceRecord.id
                    == UUID(result["occurrences"][1]["occurrence_id"])
                )
            )
            assert repeated is not None
            assert repeated.fold == "earlier"
            assert repeated.starts_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

        spring_draft = RecurringPlanDraft(
            PlanKind.TASK,
            "Sunday session",
            date(2027, 3, 7),
            time(2, 30),
            "America/New_York",
            RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(6,), count=2),
        )
        spring_proof = await producer.issue_create(owner_id, spring_draft)
        spring_result = await _create(
            recurrence_sessions,
            owner_id,
            spring_draft,
            spring_proof,
            action_key="issue10-dst-spring",
            verifier=producer,
        )
        spring_id = UUID(spring_result["series_id"])
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            spring_series = await SqlAlchemySeriesStore(uow.session).get_series_snapshot(
                owner_id, spring_id
            )
        assert spring_series is not None
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            await generate_recurrence_horizon(
                GenerateRecurrenceSeries(spring_id, spring_series.version, date(2027, 3, 20)),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                clock=FixedClock(NOW),
            )
        async with recurrence_sessions() as session:
            shifted = await session.scalar(
                select(OccurrenceRecord)
                .join(
                    SeriesOccurrenceRecord,
                    SeriesOccurrenceRecord.occurrence_id == OccurrenceRecord.id,
                )
                .where(
                    SeriesOccurrenceRecord.series_id == spring_id,
                    SeriesOccurrenceRecord.sequence == 1,
                )
            )
            assert shifted is not None
            association = await session.scalar(
                select(SeriesOccurrenceRecord).where(
                    SeriesOccurrenceRecord.series_id == spring_id,
                    SeriesOccurrenceRecord.sequence == 1,
                )
            )
            assert association is not None and association.dst_gap_shift_seconds == 3600
            assert shifted.local_time == time(3, 30)
            assert shifted.starts_at == datetime(2027, 3, 14, 7, 30, tzinfo=UTC)

        # The unrelated owner cannot locate or generate the series.
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            store = SqlAlchemySeriesStore(uow.session)
            assert await store.get_series_snapshot(uuid4(), series_id) is None
            with pytest.raises(SeriesNotFound):
                await store.generate_occurrences(
                    owner_id=uuid4(),
                    series_id=series_id,
                    expected_version=1,
                    through=NOW.date() + timedelta(days=1),
                    at=NOW,
                )

        terminal_id = UUID(result["occurrences"][0]["occurrence_id"])
        async with recurrence_sessions() as session, session.begin():
            await session.execute(
                update(OccurrenceRecord)
                .where(OccurrenceRecord.id == terminal_id, OccurrenceRecord.owner_id == owner_id)
                .values(state="completed", version=2)
            )
            await session.execute(
                update(AccessPeriodRecord)
                .where(AccessPeriodRecord.owner_id == owner_id)
                .values(
                    starts_at=NOW - timedelta(days=10),
                    ends_at=NOW - timedelta(days=1),
                )
            )

        stop_proof = await producer.issue_stop(owner_id, series_id)
        async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
            stopped = await stop_confirmed_series(
                StopRecurrenceSeries("issue10-stop", series_id, 1, 1, stop_proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemySeriesStore(uow.session),
                identity=SqlAlchemyIdentityStore(uow.session),
                billing=SqlAlchemyAccessUsageStore(uow.session),
                confirmation_verifier=producer.for_session(uow.session),
                clock=FixedClock(NOW),
            )
        assert stopped["state"] == "stopped"
        async with recurrence_sessions() as session:
            terminal = await session.get(OccurrenceRecord, terminal_id)
            future_id = UUID(result["occurrences"][1]["occurrence_id"])
            future = await session.get(OccurrenceRecord, future_id)
            assert terminal is not None and terminal.state == "completed" and terminal.version == 2
            assert future is not None and future.state == "open" and future.version == 1
        with pytest.raises(SeriesStopped):
            async with SqlAlchemyUnitOfWork(recurrence_sessions) as uow:
                await SqlAlchemySeriesStore(uow.session).generate_occurrences(
                    owner_id=owner_id,
                    series_id=series_id,
                    expected_version=2,
                    through=NOW.date() + timedelta(days=10),
                    at=NOW,
                )

    run_local_async(scenario)
