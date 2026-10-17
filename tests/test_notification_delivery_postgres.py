from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import make_url

from pin.adapters.telegram import delivery_worker as delivery_worker_module
from pin.adapters.telegram.application_worker import process_application_job
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    RecoverySummaryRequest,
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.domain.delivery import DeliveryOutcomeKind, render_reminder
from pin.contexts.notifications.infrastructure.delivery_worker import (
    DELIVERY_RECOVERY_SUMMARY_JOB_KIND,
    _reminder_delivery_is_uncertain,
    deliver_recovery_summary_job,
    deliver_reminder_job,
    deliver_reply_job,
    enqueue_due_reminder_jobs,
    enqueue_due_reply_jobs,
    enqueue_recovery_summary_job,
)
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
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
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.models import Base
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import (
    JobClaim,
    JobLane,
    LeaseLost,
    claim_job,
    complete_job,
    enqueue_job,
    receive_update,
)
from pin.platform.models import Job, OutboxEvent, RecoveryGate

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_DELIVERY_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime(2026, 10, 14, 7, tzinfo=UTC)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_DELIVERY_DATABASE_URL to the fresh owned issue-12 PostgreSQL database",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def delivery_sessions() -> Iterator[object]:
    if DATABASE_URL is None:
        pytest.skip("PIN_TEST_DELIVERY_DATABASE_URL is unset")
    url = make_url(DATABASE_URL)
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError("delivery tests require postgresql+psycopg")
    if (url.host, url.port, url.username) != ("127.0.0.1", 51017, "pin_test"):
        raise RuntimeError(
            "delivery tests refuse a database outside the dedicated local test server"
        )
    if not url.database or not url.database.startswith("pin_issue12_"):
        raise RuntimeError("delivery tests refuse databases without the pin_issue12_ prefix")
    sync_engine = create_sync_engine(url)
    try:
        with sync_engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            if directory.rstrip("/").casefold() != DATA_DIRECTORY.casefold():
                raise RuntimeError("delivery tests refuse an unexpected PostgreSQL data directory")
    finally:
        sync_engine.dispose()
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    engine = create_engine(DATABASE_URL)
    sessions = create_session_factory(engine)
    yield sessions
    run_local_async(engine.dispose)


@pytest.fixture(autouse=True)
def clean_delivery_test_rows(delivery_sessions) -> Iterator[None]:
    """Isolate this opt-in suite's rows on its strictly guarded private fixture DB."""
    async def cleanup() -> None:
        async with delivery_sessions() as session, session.begin():
            tables = [table for table in Base.metadata.sorted_tables if table.schema]
            for table in reversed(tables):
                await session.execute(delete(table))

    run_local_async(cleanup)
    yield
    run_local_async(cleanup)


class _ReplyTransport:
    def __init__(
        self,
        chat_id: int,
        *,
        fail_unknown: bool = False,
        crash_after_send: bool = False,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.fail_unknown = fail_unknown
        self.crash_after_send = crash_after_send
        self.before_send = before_send
        self.sent: list[TelegramReplySend] = []

    async def resolve_destination(self, session, owner_id):
        return self.chat_id

    async def send_reminder(self, message):
        raise AssertionError("reply fixture cannot send a reminder")

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.sent.append(message)
        if self.before_send is not None:
            await self.before_send()
        if self.crash_after_send:
            raise _SimulatedWorkerCrash
        if self.fail_unknown:
            raise UnknownDeliveryFailure("test_transport_timeout")
        return "telegram-message-42"


class _SimulatedWorkerCrash(BaseException):
    pass


@dataclass(frozen=True, slots=True)
class _ReminderFixture:
    owner_id: UUID
    intent_id: UUID
    occurrence_id: UUID
    chat_id: int


class _ReminderTransport:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        crash: bool = False,
        before_resolve: Callable[[UUID], Awaitable[None]] | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.failure = failure
        self.crash = crash
        self.before_resolve = before_resolve
        self.before_send = before_send
        self.sent: list[ReminderSend] = []
        self.started_at: list[float] = []

    async def resolve_destination(self, session, owner_id):
        if self.before_resolve is not None:
            await self.before_resolve(owner_id)
        return await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )

    async def send_reminder(self, message: ReminderSend) -> str | None:
        self.started_at.append(perf_counter())
        self.sent.append(message)
        if self.before_send is not None:
            await self.before_send()
        if self.crash:
            raise _SimulatedWorkerCrash
        if self.failure is not None:
            raise self.failure
        return f"telegram-{len(self.sent)}"

    async def send_reply(self, message: TelegramReplySend) -> str:
        raise AssertionError("reminder fixture cannot send a reply")


class _RecoverySummaryTransport(_ReminderTransport):
    def __init__(self, *, failure: Exception | None = None) -> None:
        super().__init__()
        self.failure = failure
        self.replies: list[TelegramReplySend] = []

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.replies.append(message)
        if self.failure is not None:
            raise self.failure
        return f"telegram-summary-{len(self.replies)}"


async def _seed_reminder_case(
    sessions,
    *,
    at: datetime,
    access_ends_at: datetime | None = None,
    deleted: bool = False,
    occurrence_state: str = "open",
    preference_version: int = 1,
    approved_preference_version: int = 1,
    plan_version: int = 1,
    intent_plan_version: int = 1,
    intent_dispatch_version: int = 1,
    job_dispatch_version: int | None = None,
    stopped_series: bool = False,
    exhausted_quota: bool = False,
    enqueue: bool = True,
) -> _ReminderFixture:
    owner_id, plan_id, occurrence_id, intent_id = uuid4(), uuid4(), uuid4(), uuid4()
    chat_id = uuid4().int % 2_000_000_000 + 1
    bot_id = uuid4().int % 2_000_000_000 + 1
    series_id = uuid4() if stopped_series else None
    due_at = at.astimezone(UTC) - timedelta(minutes=5)
    starts_at = at.astimezone(UTC) + timedelta(hours=3)
    ends_at = starts_at + timedelta(hours=1)
    source_event = EventEnvelope(
        id=uuid4(),
        event_type="planning.series_stopped" if stopped_series else "planning.plan_created",
        schema_version=1,
        aggregate_id=series_id or occurrence_id,
        aggregate_version=2 if stopped_series else 1,
        owner_id=owner_id,
        occurred_at=at,
        payload={},
    )
    async with sessions() as session, session.begin():
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language="en",
                home_zone="UTC",
                reminder_calendar_days=1,
                reminder_elapsed_hours=1,
                quiet_start=time(22),
                quiet_end=time(8),
                review_time=time(20),
                version=preference_version,
            )
        )
        session.add(
            TelegramOwnerRecord(bot_id=bot_id, telegram_user_id=chat_id, owner_id=owner_id)
        )
        session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=at if deleted else None))
        await session.flush()
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"issue12-{owner_id}",
                policy_kind="paid",
                policy_version="test-v1",
                starts_at=at.astimezone(UTC) - timedelta(days=1),
                ends_at=access_ends_at or at.astimezone(UTC) + timedelta(days=7),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            PlanItemRecord(
                id=plan_id,
                owner_id=owner_id,
                kind="appointment",
                title="Issue 12 eligibility",
                version=plan_version,
            )
        )
        await session.flush()
        session.add(
            OccurrenceRecord(
                id=occurrence_id,
                owner_id=owner_id,
                plan_item_id=plan_id,
                kind="appointment",
                event_zone="UTC",
                local_date=starts_at.date(),
                local_time=starts_at.time().replace(tzinfo=None),
                end_local_date=ends_at.date(),
                end_local_time=ends_at.time().replace(tzinfo=None),
                starts_at=starts_at,
                ends_at=ends_at,
                deadline_at=None,
                duration_seconds=3600,
                state=occurrence_state,
                version=1,
                reminder_preferences_version=approved_preference_version,
            )
        )
        if stopped_series:
            session.add(
                SeriesRecord(
                    id=series_id,
                    owner_id=owner_id,
                    plan_item_id=plan_id,
                    version=2,
                    state="stopped",
                    anchor_date=starts_at.date(),
                    anchor_sequence=0,
                    next_sequence=1,
                    total_count=1,
                    rule={"frequency": "daily", "interval": 1},
                    typed_draft={"title": "Issue 12 eligibility"},
                )
            )
        await session.flush()
        if stopped_series:
            session.add(
                SeriesOccurrenceRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    series_id=series_id,
                    occurrence_id=occurrence_id,
                    sequence=0,
                    recurrence_key=starts_at.date().isoformat(),
                )
            )
        session.add(
            ReminderRuleRecord(
                id=uuid4(),
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind="elapsed_seconds",
                offset_amount=3600,
                source="default",
                due_at=due_at,
            )
        )
        await add_outbox_event(session, source_event)
        session.add(
            ReminderIntentRecord(
                id=intent_id,
                owner_id=owner_id,
                plan_item_id=plan_id,
                occurrence_id=occurrence_id,
                series_id=series_id,
                plan_version=intent_plan_version,
                occurrence_version=1,
                preferences_version=approved_preference_version,
                series_version=2 if stopped_series else None,
                offset_kind="elapsed_seconds",
                offset_amount=3600,
                source="default",
                due_at=due_at,
                not_before_at=None,
                state="scheduled",
                dispatch_version=intent_dispatch_version,
                source_event_id=source_event.id,
            )
        )
        if exhausted_quota:
            session.add(
                UsageReservationRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    period_id=f"issue12-{owner_id}",
                    request_id="issue12-exhausted",
                    reserved_ai_turns=250,
                    reserved_voice_seconds=1,
                    settled_ai_turns=250,
                    settled_voice_seconds=0,
                    state="settled",
                    created_at=at.astimezone(UTC) - timedelta(hours=1),
                    expires_at=at.astimezone(UTC) + timedelta(hours=1),
                    finalized_at=at.astimezone(UTC),
                )
            )
        if enqueue:
            enqueued_version = job_dispatch_version or intent_dispatch_version
            await enqueue_job(
                session,
                lane=JobLane.DELIVERY,
                kind="notifications.deliver_reminder",
                payload={
                    "intent_id": str(intent_id),
                    "dispatch_version": enqueued_version,
                },
                deduplication_key=f"reminder:{intent_id}:{enqueued_version}",
                owner_id=owner_id,
                priority=1_000_000,
            )
    return _ReminderFixture(owner_id, intent_id, occurrence_id, chat_id)


def test_restored_regenerated_delivery_is_unknown_and_postrestore_change_is_fresh(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        snapshot_at = datetime(2026, 10, 16, 12, tzinfo=UTC)
        fenced_at = datetime.now(UTC) + timedelta(seconds=5)
        fixture = await _seed_reminder_case(delivery_sessions, at=fenced_at)
        async with delivery_sessions() as session, session.begin():
            intent = await session.get(ReminderIntentRecord, fixture.intent_id)
            assert intent is not None
            old_source_event_id = intent.source_event_id
            series_id = uuid4()
            await session.execute(
                update(PlanItemRecord)
                .where(PlanItemRecord.id == intent.plan_item_id)
                .values(created_at=snapshot_at - timedelta(days=1))
            )
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == old_source_event_id)
                .values(
                    created_at=fenced_at + timedelta(minutes=1),
                    event_type="planning.series_occurrence_created",
                    aggregate_id=fixture.occurrence_id,
                )
            )
            session.add(
                SeriesRecord(
                    id=series_id,
                    owner_id=fixture.owner_id,
                    plan_item_id=intent.plan_item_id,
                    version=1,
                    state="active",
                    anchor_date=date(2026, 10, 16),
                    anchor_sequence=0,
                    next_sequence=1,
                    rule={},
                    typed_draft={},
                    created_at=snapshot_at - timedelta(days=1),
                    updated_at=snapshot_at - timedelta(days=1),
                )
            )
            await session.flush()
            await session.execute(
                update(ReminderIntentRecord)
                .where(ReminderIntentRecord.id == fixture.intent_id)
                .values(
                    created_at=snapshot_at + timedelta(minutes=1),
                    series_id=series_id,
                    series_version=1,
                )
            )
            session.add(
                SeriesOccurrenceRecord(
                    owner_id=fixture.owner_id,
                    series_id=series_id,
                    occurrence_id=fixture.occurrence_id,
                    sequence=0,
                    recurrence_key="2026-10-16",
                )
            )
            session.add(
                RecoveryGate(
                    singleton=True,
                    state="ready",
                    snapshot_id="pin-20261016T120000Z",
                    ledger_id="pin-20261017T120000Z",
                    snapshot_at=snapshot_at,
                    fenced_at=fenced_at,
                    released_at=fenced_at,
                )
            )
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="recovery-lost-window",
                lease_for=timedelta(minutes=2),
            )
            assert claim is not None

        transport = _ReminderTransport()
        outcome = await deliver_reminder_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: fenced_at,
        )
        assert outcome is not None and outcome.outcome is DeliveryOutcomeKind.UNKNOWN
        assert transport.sent == []
        async with delivery_sessions() as session, session.begin():
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None
            assert attempt.outcome == DeliveryOutcomeKind.UNKNOWN.value
            assert attempt.failure_code == "recovery_uncertain_window"

            fresh_source_event = EventEnvelope(
                id=uuid4(),
                event_type="planning.plan_rescheduled",
                schema_version=1,
                aggregate_id=fixture.occurrence_id,
                aggregate_version=2,
                owner_id=fixture.owner_id,
                occurred_at=fenced_at + timedelta(seconds=1),
                payload={},
            )
            await add_outbox_event(session, fresh_source_event)
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == fresh_source_event.id)
                .values(created_at=fenced_at + timedelta(seconds=1))
            )
            await session.execute(
                update(ReminderIntentRecord)
                .where(ReminderIntentRecord.id == fixture.intent_id)
                .values(source_event_id=fresh_source_event.id)
            )
            intent = await session.get(ReminderIntentRecord, fixture.intent_id)
            assert intent is not None
            assert not await _reminder_delivery_is_uncertain(
                session, intent, SqlAlchemyIdentityStore(session)
            )

    run_local_async(scenario)


def test_bounded_reminder_pump_liveness_and_queue_microbenchmark(
    delivery_sessions, request: pytest.FixtureRequest
) -> None:
    async def scenario() -> None:
        owner_id, plan_id = uuid4(), uuid4()
        batch_count, batch_size = 20, 101
        intent_ids = [uuid4() for _ in range(batch_count * batch_size)]
        occurrence_ids = [uuid4() for _ in intent_ids]
        async with delivery_sessions() as session, session.begin():
            session.add(
                PlanItemRecord(
                    id=plan_id, owner_id=owner_id, kind="untimed", title="Batch test"
                )
            )
            await session.flush()
            session.add_all(
                OccurrenceRecord(
                    id=occurrence_id,
                    owner_id=owner_id,
                    plan_item_id=plan_id,
                    kind="untimed",
                    event_zone="UTC",
                    local_date=date(2026, 10, 14),
                    local_time=None,
                    starts_at=None,
                    ends_at=None,
                    deadline_at=None,
                    duration_seconds=None,
                    state="open",
                    version=1,
                )
                for occurrence_id in occurrence_ids
            )
            await session.flush()
            session.add_all(
                ReminderIntentRecord(
                    id=intent_id,
                    owner_id=owner_id,
                    plan_item_id=plan_id,
                    occurrence_id=occurrence_id,
                    series_id=None,
                    plan_version=1,
                    occurrence_version=1,
                    preferences_version=1,
                    series_version=None,
                    offset_kind="elapsed_seconds",
                    offset_amount=3600,
                    source="default",
                    due_at=NOW - timedelta(minutes=batch_count - index // batch_size),
                    state="scheduled",
                    dispatch_version=1,
                    source_event_id=uuid4(),
                )
                for index, (intent_id, occurrence_id) in enumerate(
                    zip(intent_ids, occurrence_ids, strict=True)
                )
            )
            session.add_all(
                Job(
                    lane=JobLane.DELIVERY.value,
                    kind="notifications.deliver_reminder",
                    payload={"intent_id": str(intent_id), "dispatch_version": 1},
                    deduplication_key=f"reminder:{intent_id}:1",
                    owner_id=owner_id,
                    status="succeeded",
                    attempts=1,
                    max_attempts=8,
                    finished_at=NOW,
                )
                for index in range(batch_count)
                for intent_id in intent_ids[
                    index * batch_size : index * batch_size + batch_size - 1
                ]
            )
        dispatch_samples: list[float] = []
        for index in range(batch_count):
            started_at = perf_counter()
            async with delivery_sessions() as session, session.begin():
                assert await enqueue_due_reminder_jobs(session, now=NOW, limit=1) == 1
                claim = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id=f"issue12-load-{index}",
                    lease_for=timedelta(minutes=2),
                )
                expected_id = intent_ids[index * batch_size + batch_size - 1]
                assert claim is not None and claim.payload["intent_id"] == str(expected_id)
                await complete_job(session, claim)
            dispatch_samples.append(perf_counter() - started_at)
        p95_seconds = sorted(dispatch_samples)[ceil(0.95 * len(dispatch_samples)) - 1]
        request.node.user_properties.append(
            ("pump_claim_p95_seconds", f"{p95_seconds:.3f}")
        )

    run_local_async(scenario)


def test_outer_delivery_authorizes_current_open_series_after_quota_exhaustion(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(
            delivery_sessions,
            at=at,
            stopped_series=True,
            exhausted_quota=True,
        )
        transport = _ReminderTransport()
        assert await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id="issue12-active-series",
            now=lambda: at,
        )
        assert len(transport.sent) == 1
        assert transport.sent[0].destination_id == fixture.chat_id
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None
            assert attempt.outcome == "confirmed"
            assert attempt.failure_code is None

    run_local_async(scenario)


@pytest.mark.parametrize(
    ("case", "expected_failure"),
    (
        ("expired", "access_expired"),
        ("deleted", "account_deleted"),
        ("completed", "obsolete_or_incomplete_schedule"),
        ("preferences_changed", "obsolete_or_incomplete_schedule"),
        ("plan_changed", "obsolete_or_incomplete_schedule"),
        ("stale_dispatch", "stale_intent_version"),
    ),
)
def test_outer_delivery_suppresses_expired_deleted_or_obsolete_reminders(
    delivery_sessions, case: str, expected_failure: str
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        kwargs: dict[str, object] = {}
        if case == "expired":
            kwargs["access_ends_at"] = at - timedelta(seconds=1)
        elif case == "deleted":
            kwargs["deleted"] = True
        elif case == "completed":
            kwargs["occurrence_state"] = "completed"
        elif case == "preferences_changed":
            kwargs["preference_version"] = 2
        elif case == "plan_changed":
            kwargs["plan_version"] = 2
        elif case == "stale_dispatch":
            kwargs["intent_dispatch_version"] = 2
            kwargs["job_dispatch_version"] = 1
        fixture = await _seed_reminder_case(delivery_sessions, at=at, **kwargs)
        transport = _ReminderTransport()
        assert await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id=f"issue12-{case}",
            now=lambda: at,
        )
        assert transport.sent == []
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None
            assert attempt.outcome == "skipped"
            assert attempt.failure_code == expected_failure
            if case == "stale_dispatch":
                intent = await session.get(ReminderIntentRecord, fixture.intent_id)
                assert intent is not None and intent.dispatch_version == 2
                assert attempt.dispatch_version == 1

    run_local_async(scenario)


@pytest.mark.parametrize(
    ("failure", "expected", "delayed"),
    (
        (None, "confirmed", False),
        (KnownDeliveryFailure("telegram_429", retry_after=timedelta(minutes=1)), "failed", True),
        (KnownDeliveryFailure("bad_request"), "failed", False),
        (KnownDeliveryFailure("destination_forbidden", blocked=True), "blocked", False),
        (UnknownDeliveryFailure("provider_timeout"), "unknown", False),
    ),
)
def test_actual_reminder_transport_failure_classes_are_durable_and_not_replayed(
    delivery_sessions, failure, expected: str, delayed: bool
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(delivery_sessions, at=at)
        transport = _ReminderTransport(failure=failure)
        assert await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id=f"issue12-failure-{expected}",
            now=lambda: at,
        )
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            job = await session.scalar(
                select(Job).where(
                    Job.deduplication_key == f"reminder:{fixture.intent_id}:1"
                )
            )
            assert attempt is not None and attempt.outcome == expected
            assert job is not None
            assert (job.status == "pending") is delayed
            if delayed:
                assert job.available_at is not None
        await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id="issue12-no-replay",
            now=lambda: at,
        )
        assert sum(message.destination_id == fixture.chat_id for message in transport.sent) == 1

    run_local_async(scenario)


@pytest.mark.parametrize("change", ("complete", "expire", "delete"))
def test_change_committed_before_authoritative_check_prevents_send(
    delivery_sessions, change: str
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(delivery_sessions, at=at)

        async def mutate_before_check(owner_id: UUID) -> None:
            async with delivery_sessions() as session, session.begin():
                if change == "complete":
                    row = await session.get(OccurrenceRecord, fixture.occurrence_id)
                    assert row is not None
                    row.state = "completed"
                    row.version += 1
                elif change == "expire":
                    period = await session.scalar(
                        select(AccessPeriodRecord).where(
                            AccessPeriodRecord.owner_id == owner_id
                        )
                    )
                    assert period is not None
                    period.ends_at = at - timedelta(seconds=1)
                else:
                    account = await session.get(AccessAccountRecord, owner_id)
                    assert account is not None
                    account.deleted_at = at

        transport = _ReminderTransport(before_resolve=mutate_before_check)
        assert await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id=f"issue12-precheck-{change}",
            now=lambda: at,
        )
        assert transport.sent == []
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None and attempt.outcome == "skipped"
            assert attempt.failure_code in {
                "obsolete_or_incomplete_schedule",
                "access_expired",
                "account_deleted",
            }

    run_local_async(scenario)


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_postcheck_terminal_occurrence_waits_for_attempt_authorization_then_may_arrive(
    delivery_sessions, state: str
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(delivery_sessions, at=at)
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-postcheck",
                lease_for=timedelta(minutes=2),
            )
            assert claim is not None and claim.payload["intent_id"] == str(fixture.intent_id)

        mutation_tasks: list[asyncio.Task[None]] = []

        async def change_after_check() -> None:
            async with delivery_sessions() as session, session.begin():
                row = await session.get(OccurrenceRecord, fixture.occurrence_id)
                assert row is not None
                row.state = state
                row.version += 1

        async def pause_before_authorization() -> None:
            task = asyncio.create_task(change_after_check())
            mutation_tasks.append(task)
            await asyncio.sleep(0.05)
            assert not task.done(), "current schedule row lock must fence the edit"

        async def wait_for_edit() -> None:
            await mutation_tasks[0]

        transport = _ReminderTransport(before_send=wait_for_edit)
        outcome = await deliver_reminder_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: at,
            before_attempt_authorization=pause_before_authorization,
        )
        assert outcome is not None and outcome.outcome.value == "confirmed"
        assert len(transport.sent) == 1
        async with delivery_sessions() as session:
            row = await session.get(OccurrenceRecord, fixture.occurrence_id)
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert row is not None and row.state == state
            assert attempt is not None and attempt.outcome == "confirmed"

    run_local_async(scenario)


@pytest.mark.parametrize("mutation", ["delete_account", "expire_period"])
def test_postcheck_access_mutation_serializes_behind_started_authorization(
    delivery_sessions, mutation: str
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(
            delivery_sessions,
            at=at,
            access_ends_at=at + timedelta(minutes=5),
        )
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id=f"issue12-{mutation}",
                lease_for=timedelta(minutes=2),
            )
            assert claim is not None

        mutation_tasks: list[asyncio.Task[None]] = []

        async def change_access() -> None:
            async with delivery_sessions() as session, session.begin():
                if mutation == "delete_account":
                    account = await session.get(AccessAccountRecord, fixture.owner_id)
                    assert account is not None
                    account.deleted_at = at
                else:
                    period = await session.scalar(
                        select(AccessPeriodRecord).where(
                            AccessPeriodRecord.owner_id == fixture.owner_id
                        )
                    )
                    assert period is not None
                    period.ends_at = at

        async def pause_before_authorization() -> None:
            task = asyncio.create_task(change_access())
            mutation_tasks.append(task)
            await asyncio.sleep(0.05)
            assert not task.done(), "billing row lock must fence access mutation"

        async def wait_for_access_mutation() -> None:
            await mutation_tasks[0]

        transport = _ReminderTransport(before_send=wait_for_access_mutation)
        outcome = await deliver_reminder_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: at,
            before_attempt_authorization=pause_before_authorization,
        )
        assert outcome is not None and outcome.outcome.value == "confirmed"
        assert len(transport.sent) == 1
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None and attempt.outcome == "confirmed"
            if mutation == "delete_account":
                account = await session.get(AccessAccountRecord, fixture.owner_id)
                assert account is not None and account.deleted_at is not None
            else:
                period = await session.scalar(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == fixture.owner_id
                    )
                )
                assert period is not None and period.ends_at == at

    run_local_async(scenario)


def test_entitlement_expiry_at_authorization_boundary_blocks_send(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        ends_at = at + timedelta(minutes=5)
        fixture = await _seed_reminder_case(
            delivery_sessions, at=at, access_ends_at=ends_at
        )
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-natural-expiry",
                lease_for=timedelta(minutes=2),
            )
            assert claim is not None

        clock = [at]

        async def cross_expiry() -> None:
            clock[0] = ends_at + timedelta(microseconds=1)

        transport = _ReminderTransport()
        outcome = await deliver_reminder_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: clock[0],
            before_attempt_authorization=cross_expiry,
        )
        assert outcome is not None and outcome.outcome.value == "skipped"
        assert outcome.failure_code == "access_expired"
        assert transport.sent == []
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == fixture.intent_id
                )
            )
            assert attempt is not None and attempt.outcome == "skipped"
            assert attempt.failure_code == "access_expired"

    run_local_async(scenario)


def test_expired_worker_lease_at_authorization_boundary_cannot_start_send(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(delivery_sessions, at=at)
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-boundary-expiry",
                lease_for=timedelta(seconds=1),
            )
            assert claim is not None

        async def wait_past_lease() -> None:
            await asyncio.sleep(1.1)

        transport = _ReminderTransport()
        with pytest.raises(LeaseLost):
            await deliver_reminder_job(
                delivery_sessions,
                claim,
                transport,
                identity_store_factory=SqlAlchemyIdentityStore,
                now=lambda: at,
                before_attempt_authorization=wait_past_lease,
            )
        assert transport.sent == []
        async with delivery_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.intent_id == fixture.intent_id
                        )
                    )
                ).all()
            )
            assert attempts == []

    run_local_async(scenario)


def test_reclaimed_reminder_lease_cannot_send_or_finalize_replacement_attempt(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(delivery_sessions, at=at)
        async with delivery_sessions() as session, session.begin():
            stale_claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-stale-worker",
                lease_for=timedelta(minutes=2),
            )
            assert stale_claim is not None
            await session.execute(
                update(Job)
                .where(Job.id == stale_claim.job_id)
                .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
            )
        async with delivery_sessions() as session, session.begin():
            replacement_claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-replacement-worker",
                lease_for=timedelta(minutes=2),
            )
            assert replacement_claim is not None
            assert replacement_claim.job_id == stale_claim.job_id

        transport = _ReminderTransport()
        with pytest.raises(LeaseLost):
            await deliver_reminder_job(
                delivery_sessions,
                stale_claim,
                transport,
                identity_store_factory=SqlAlchemyIdentityStore,
                now=lambda: at,
            )
        assert transport.sent == []
        crashing_transport = _ReminderTransport(crash=True)
        with pytest.raises(_SimulatedWorkerCrash):
            await deliver_reminder_job(
                delivery_sessions,
                replacement_claim,
                crashing_transport,
                identity_store_factory=SqlAlchemyIdentityStore,
                now=lambda: at,
            )
        async with delivery_sessions() as session, session.begin():
            await session.execute(
                update(Job)
                .where(Job.id == replacement_claim.job_id)
                .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
            )
        async with delivery_sessions() as session, session.begin():
            recovery_claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-recovery-worker",
                lease_for=timedelta(minutes=2),
            )
            assert recovery_claim is not None
        with pytest.raises(LeaseLost):
            await deliver_reminder_job(
                delivery_sessions,
                replacement_claim,
                _ReminderTransport(),
                identity_store_factory=SqlAlchemyIdentityStore,
                now=lambda: at,
            )
        recovered = await deliver_reminder_job(
            delivery_sessions,
            recovery_claim,
            _ReminderTransport(),
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: at,
        )
        assert recovered is not None and recovered.outcome.value == "unknown"
        assert len(crashing_transport.sent) == 1
        async with delivery_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.intent_id == fixture.intent_id
                        )
                    )
                ).all()
            )
            assert len(attempts) == 1 and attempts[0].outcome == "unknown"

    run_local_async(scenario)


def test_ready_delivery_runs_due_sweep_after_dispatch_without_skipping_it(
    delivery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        await _seed_reminder_case(delivery_sessions, at=at, enqueue=False)
        async with delivery_sessions() as session, session.begin():
            assert await enqueue_due_reminder_jobs(session, now=at) == 1

        transport = _ReminderTransport()
        original_schedule = delivery_worker_module._schedule_due_work
        sweep_after_dispatch = False

        async def observe_schedule(session, observed_transport, *, at):
            nonlocal sweep_after_dispatch
            assert observed_transport is transport
            assert len(transport.started_at) == 1
            sweep_after_dispatch = True
            await original_schedule(session, observed_transport, at=at)

        monkeypatch.setattr(delivery_worker_module, "_schedule_due_work", observe_schedule)
        processed = await process_delivery_iteration(
            delivery_sessions,
            transport,
            worker_id="issue20-maintenance-fairness",
            now=lambda: at,
        )
        assert processed
        assert len(transport.sent) == 1
        assert sweep_after_dispatch

    run_local_async(scenario)


def test_actual_outer_loop_dispatch_start_p95_for_50_due_owners(
    delivery_sessions, request: pytest.FixtureRequest
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        owners = [
            await _seed_reminder_case(delivery_sessions, at=at, enqueue=False)
            for _ in range(50)
        ]
        expected_destinations = {owner.chat_id for owner in owners}
        transport = _ReminderTransport()
        due_arrival = perf_counter()
        async with delivery_sessions() as session, session.begin():
            assert await enqueue_due_reminder_jobs(session, now=at) == len(owners)
        worker_index = 0
        def expected_dispatch_starts() -> list[float]:
            return [
                started
                for started, message in zip(
                    transport.started_at, transport.sent, strict=True
                )
                if message.destination_id in expected_destinations
            ]

        while (
            len(expected_dispatch_starts()) < len(owners)
            and perf_counter() - due_arrival < 10
        ):
            results = await asyncio.gather(
                *(
                    process_delivery_iteration(
                        delivery_sessions,
                        transport,
                        worker_id=f"issue12-profile-{worker_index + offset}",
                        now=lambda: at,
                    )
                    for offset in range(len(owners))
                )
            )
            worker_index += len(owners)
            if not any(results):
                await asyncio.sleep(0.01)
        delays = [started - due_arrival for started in expected_dispatch_starts()]
        p95_seconds = sorted(delays)[ceil(0.95 * len(delays)) - 1]
        request.node.user_properties.append(
            ("actual_transport_dispatch_start_p95_seconds", f"{p95_seconds:.3f}")
        )
        assert len(delays) == len(owners)
        assert p95_seconds <= 10, (
            "actual send invocation p95 exceeded 10 seconds for 50 independent due owners: "
            f"{p95_seconds:.3f}s"
        )

    run_local_async(scenario)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, "confirmed"),
        (UnknownDeliveryFailure("summary_timeout"), "unknown"),
    ],
)
def test_recovery_summary_uses_current_overdue_rows_and_never_replays_terminal_send(
    delivery_sessions, failure: Exception | None, expected: str
) -> None:
    async def scenario() -> None:
        at = NOW + timedelta(hours=2)
        fixture = await _seed_reminder_case(
            delivery_sessions, at=at, enqueue=False
        )
        async with delivery_sessions() as session, session.begin():
            appointment = await session.get(OccurrenceRecord, fixture.occurrence_id)
            assert appointment is not None
            appointment.starts_at = at - timedelta(hours=3)
            appointment.ends_at = at - timedelta(hours=2)
            appointment.local_date = (at - timedelta(hours=3)).date()
            plan = await session.get(PlanItemRecord, appointment.plan_item_id)
            assert plan is not None
            plan.title = "Past appointment"

            # An untimed task has no reminder intent, yet belongs in a current
            # owner-scoped recovery summary while it remains open and overdue.
            untimed_plan_id = uuid4()
            session.add(
                PlanItemRecord(
                    id=untimed_plan_id,
                    owner_id=fixture.owner_id,
                    kind="untimed",
                    title="Untimed task",
                )
            )
            for title in ("Completed item", "Cancelled item"):
                session.add(
                    PlanItemRecord(
                        id=uuid4(),
                        owner_id=fixture.owner_id,
                        kind="appointment",
                        title=title,
                    )
                )
            other_owner_id, other_plan_id = uuid4(), uuid4()
            session.add(
                PlanItemRecord(
                    id=other_plan_id,
                    owner_id=other_owner_id,
                    kind="untimed",
                    title="Other owner's item",
                )
            )
            await session.flush()
            session.add(
                OccurrenceRecord(
                    id=uuid4(),
                    owner_id=fixture.owner_id,
                    plan_item_id=untimed_plan_id,
                    kind="untimed",
                    event_zone="UTC",
                    local_date=at.date() - timedelta(days=1),
                    local_time=None,
                    starts_at=None,
                    ends_at=None,
                    deadline_at=None,
                    duration_seconds=None,
                    state="open",
                    version=1,
                )
            )

            extra_plans = list(
                (
                    await session.scalars(
                        select(PlanItemRecord).where(
                            PlanItemRecord.owner_id == fixture.owner_id,
                            PlanItemRecord.title.in_(("Completed item", "Cancelled item")),
                        )
                    )
                ).all()
            )
            for plan, state in zip(extra_plans, ("completed", "cancelled"), strict=True):
                session.add(
                    OccurrenceRecord(
                        id=uuid4(),
                        owner_id=fixture.owner_id,
                        plan_item_id=plan.id,
                        kind="appointment",
                        event_zone="UTC",
                        local_date=(at - timedelta(days=1)).date(),
                        local_time=time(8),
                        end_local_date=(at - timedelta(days=1)).date(),
                        end_local_time=time(9),
                        starts_at=at - timedelta(days=1, hours=2),
                        ends_at=at - timedelta(days=1, hours=1),
                        deadline_at=None,
                        duration_seconds=3600,
                        state=state,
                        version=1,
                    )
                )

            session.add(
                OccurrenceRecord(
                    id=uuid4(),
                    owner_id=other_owner_id,
                    plan_item_id=other_plan_id,
                    kind="untimed",
                    event_zone="UTC",
                    local_date=at.date() - timedelta(days=1),
                    local_time=None,
                    starts_at=None,
                    ends_at=None,
                    deadline_at=None,
                    duration_seconds=None,
                    state="open",
                    version=1,
                )
            )
            intent = await session.get(ReminderIntentRecord, fixture.intent_id)
            assert intent is not None
            intent.due_at = at + timedelta(days=1)

        request = RecoverySummaryRequest(fixture.owner_id, "recovered-episode-42")
        job_key = f"recovery-summary:{fixture.owner_id}:{request.episode_key}"
        async with delivery_sessions() as session, session.begin():
            first = await enqueue_recovery_summary_job(session, request)
            duplicate = await enqueue_recovery_summary_job(session, request)
            assert first.id == duplicate.id
            assert first.kind == DELIVERY_RECOVERY_SUMMARY_JOB_KIND
            first.priority = 2_000_000_000

        transport = _RecoverySummaryTransport(failure=failure)
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-recovery-summary",
                lease_for=timedelta(minutes=2),
            )
            assert (
                claim is not None
                and claim.kind == DELIVERY_RECOVERY_SUMMARY_JOB_KIND
                and claim.owner_id == fixture.owner_id
                and claim.payload["episode_key"] == request.episode_key
            )
        await deliver_recovery_summary_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: at,
        )
        assert len(transport.replies) == 1
        message = transport.replies[0].plain_text
        assert "Past appointment" in message
        assert "Untimed task" in message
        assert "Completed item" not in message
        assert "Cancelled item" not in message
        assert "Other owner's item" not in message
        assert "check-in" not in message.casefold()

        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.owner_id == fixture.owner_id,
                    DeliveryAttemptRecord.recovery_episode_key == request.episode_key,
                )
            )
            job = await session.scalar(
                select(Job).where(Job.deduplication_key == job_key)
            )
            assert attempt is not None and attempt.outcome == expected
            assert attempt.intent_id is None and attempt.reply_event_id is None
            assert attempt.check_in_cycle_id is None
            assert job is not None and job.status == "succeeded"

        # Simulate a durable-job replay after a terminal provider result. The
        # semantic owner/episode attempt receipt prevents a second send for
        # both confirmed and unknowable external outcomes.
        async with delivery_sessions() as session, session.begin():
            replay_job = await session.scalar(
                select(Job)
                .where(Job.deduplication_key == job_key)
                .with_for_update()
            )
            assert replay_job is not None
            replay_job.status = "pending"
            replay_job.finished_at = None
            replay_job.available_at = NOW - timedelta(days=1)
            replay_job.worker_id = None
            replay_job.lease_token = None
            replay_job.lease_until = None

        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-recovery-summary-replay",
                lease_for=timedelta(minutes=2),
            )
            assert (
                claim is not None
                and claim.kind == DELIVERY_RECOVERY_SUMMARY_JOB_KIND
                and claim.owner_id == fixture.owner_id
                and claim.payload["episode_key"] == request.episode_key
            )
        await deliver_recovery_summary_job(
            delivery_sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=lambda: at,
        )
        assert len(transport.replies) == 1
        async with delivery_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.owner_id == fixture.owner_id,
                            DeliveryAttemptRecord.recovery_episode_key == request.episode_key,
                        )
                    )
                ).all()
            )
            assert len(attempts) == 1 and attempts[0].outcome == expected

    run_local_async(scenario)


def test_reply_deletion_after_authoritative_check_waits_for_started_boundary(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        owner_id, event_id = uuid4(), uuid4()
        chat_id = uuid4().int % 2_000_000_000 + 1
        at = datetime.now(UTC).replace(microsecond=0)
        async with delivery_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=991, telegram_user_id=chat_id, owner_id=owner_id
                )
            )
            session.add(AccessAccountRecord(owner_id=owner_id))
            event = EventEnvelope(
                id=event_id,
                event_type="telegram.reply.requested",
                schema_version=1,
                aggregate_id=owner_id,
                aggregate_version=1,
                owner_id=owner_id,
                occurred_at=at,
                payload={
                    "chat_id": chat_id,
                    "markdown": "Onboarding reply",
                    "plain_text": "Onboarding reply",
                },
            )
            await add_outbox_event(session, event)
            await session.flush()
            persisted_event = await session.get(OutboxEvent, event_id)
            assert persisted_event is not None
            at = persisted_event.created_at
            assert await enqueue_due_reply_jobs(session, now=at) == 1
            reply_job = await session.scalar(
                select(Job).where(Job.deduplication_key == f"reply:{event_id}")
            )
            assert reply_job is not None
            reply_job.priority = 2_000_000_000
        async with delivery_sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-reply-delete-boundary",
                lease_for=timedelta(minutes=2),
            )
            assert claim is not None and claim.kind == "notifications.deliver_reply"

        mutation_tasks: list[asyncio.Task[None]] = []

        async def delete_owner() -> None:
            async with delivery_sessions() as session, session.begin():
                account = await session.get(AccessAccountRecord, owner_id)
                assert account is not None
                account.deleted_at = at

        async def pause_before_authorization() -> None:
            task = asyncio.create_task(delete_owner())
            mutation_tasks.append(task)
            await asyncio.sleep(0.05)
            assert not task.done(), "reply lifecycle lock must fence account deletion"

        async def wait_for_delete() -> None:
            await mutation_tasks[0]

        transport = _ReplyTransport(chat_id, before_send=wait_for_delete)
        outcome = await deliver_reply_job(
            delivery_sessions,
            claim,
            transport,
            now=lambda: at,
            before_attempt_authorization=pause_before_authorization,
        )
        assert outcome is not None and outcome.outcome.value == "confirmed"
        assert len(transport.sent) == 1
        assert transport.sent[0].plain_text == "Onboarding reply"
        async with delivery_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.reply_event_id == event_id
                )
            )
            account = await session.get(AccessAccountRecord, owner_id)
            assert attempt is not None and attempt.outcome == "confirmed"
            assert account is not None and account.deleted_at is not None

    run_local_async(scenario)


def test_reply_event_is_unwrapped_and_unknown_outcome_is_never_replayed(
    delivery_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        bot_id = uuid4().int % 2_000_000_000 + 1
        chat_id = uuid4().int % 2_000_000_000 + 1
        update_id = uuid4().int % 2_000_000_000
        async with delivery_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id, telegram_user_id=chat_id, owner_id=owner_id
                )
            )
            await receive_update(
                session,
                bot_id=bot_id,
                update_id=update_id,
                owner_id=owner_id,
                update_type="message",
                payload={
                    "update_id": update_id,
                    "message": {
                        "from": {"id": chat_id},
                        "chat": {"id": chat_id, "type": "private"},
                        "text": "/start",
                    },
                },
                conversation_ordered=False,
                priority=1_000_000,
            )
        async with delivery_sessions() as session, session.begin():
            application_claim = await claim_job(
                session,
                lane=JobLane.APPLICATION,
                worker_id="issue12-app-test",
                lease_for=timedelta(minutes=2),
            )
            assert application_claim is not None
        await process_application_job(
            delivery_sessions,
            application_claim,
            now=NOW,
        )
        async with delivery_sessions() as session:
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.owner_id == owner_id,
                )
            )
            assert event is not None
        async with delivery_sessions() as session, session.begin():
            await session.execute(
                update(Job)
                .where(
                    Job.lane == JobLane.DELIVERY.value,
                    Job.status.in_(("pending", "leased")),
                )
                .values(
                    status="succeeded",
                    finished_at=NOW,
                    worker_id=None,
                    lease_token=None,
                    lease_until=None,
                )
            )

        transport = _ReplyTransport(chat_id, crash_after_send=True)
        with pytest.raises(_SimulatedWorkerCrash):
            await process_delivery_iteration(
                delivery_sessions,
                transport,
                worker_id="issue12-delivery-test",
                now=lambda: datetime.now(UTC),
            )
        async with delivery_sessions() as session, session.begin():
            started = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.reply_event_id == event.id
                )
            )
            assert started is not None and started.outcome == "started"
            claim_row = await session.scalar(
                select(Job).where(Job.deduplication_key == f"reply:{event.id}")
            )
            assert claim_row is not None
            stale_claim = JobClaim(
                job_id=claim_row.id,
                lane=JobLane.DELIVERY,
                kind=claim_row.kind,
                payload_version=claim_row.payload_version,
                payload=claim_row.payload,
                owner_id=claim_row.owner_id,
                conversation_sequence=claim_row.conversation_sequence,
                attempt=claim_row.attempts,
                worker_id=claim_row.worker_id or "",
                token=claim_row.lease_token,
                fencing_generation=None,
                lease_until=claim_row.lease_until,
            )
            await session.execute(
                update(Job)
                .where(Job.id == claim_row.id)
                .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
            )

        async with delivery_sessions() as session, session.begin():
            replacement_claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id="issue12-reclaimer",
                lease_for=timedelta(minutes=2),
            )
            assert replacement_claim is not None
            assert replacement_claim.job_id == claim_row.id

        with pytest.raises(LeaseLost):
            await deliver_reply_job(
                delivery_sessions,
                stale_claim,
                _ReplyTransport(chat_id),
                now=lambda: NOW,
            )

        recovery_transport = _ReplyTransport(chat_id)
        outcome = await deliver_reply_job(
            delivery_sessions,
            replacement_claim,
            recovery_transport,
            now=lambda: NOW,
        )
        assert outcome is not None and outcome.outcome.value == "unknown"
        assert outcome.reply_event_id == event.id
        assert len(transport.sent) == 1
        assert recovery_transport.sent == []

        async with delivery_sessions() as session, session.begin():
            assert await enqueue_due_reply_jobs(session, now=NOW) == 0
        async with delivery_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.reply_event_id == event.id
                        )
                    )
                ).all()
            )
            assert len(attempts) == 1 and attempts[0].outcome == "unknown"

    run_local_async(scenario)


def test_plain_reminder_renderer_keeps_title_text_plain() -> None:
    assert render_reminder("A *literal* title", "en") == "Reminder: A *literal* title"
