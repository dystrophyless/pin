from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram import delivery_worker as telegram_delivery_worker
from pin.adapters.telegram.accountability import process_accountability_action
from pin.adapters.telegram.accountability_composition import (
    PlanningAccountabilityReads,
    read_review_profile,
    render_accountability_message,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.mutation_runtime import process_mutation_callback
from pin.adapters.telegram.mutations import MUTATION_CALLBACK_PREFIX
from pin.contexts.accountability.api import (
    OpenUntimedItem,
    ReviewProfile,
    authorize_accountability_send,
    consume_delivery_outcomes,
    consume_planning_cycle_events,
    schedule_due_accountability,
)
from pin.contexts.accountability.application.contracts import CurrentOccurrence
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityIntentRecord,
    AccountabilityOutcomeReceiptRecord,
    CheckInCycleRecord,
    DailyReviewRecord,
)
from pin.contexts.accountability.infrastructure.scheduler import (
    apply_delivery_outcome,
    synchronize_occurrence,
)
from pin.contexts.billing.api import AccessDecision, CheckAccess, SqlAlchemyAccessUsageStore
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.api import ACCOUNTABILITY_DELIVERY_JOB_KIND
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.domain.delivery import DeliveryOutcomeKind
from pin.contexts.notifications.infrastructure.accountability_delivery import (
    deliver_accountability_job,
)
from pin.contexts.notifications.infrastructure.delivery_worker import DELIVERY_LEASE
from pin.contexts.notifications.infrastructure.models import (
    AccountabilityDeliveryAttemptRecord,
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobClaim, JobLane, claim_job, enqueue_job
from pin.platform.models import Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue16_runtime_"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


def _database_url() -> str:
    value = os.getenv("PIN_TEST_ACCOUNTABILITY_RUNTIME_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_ACCOUNTABILITY_RUNTIME_DATABASE_URL to the owned runtime database"
        )
    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
            version = str(connection.exec_driver_sql("show server_version").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX) or name == "pin_model_adapter_20261013":
        pytest.fail("Accountability runtime proof requires its own pin_issue16_runtime_ database")
    if user != DATABASE_USER or port != DATABASE_PORT:
        pytest.fail("Accountability runtime proof connected to the wrong PostgreSQL user or port")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail(
            "Accountability runtime proof connected outside the assigned disposable cluster"
        )
    if version != "18.6":
        pytest.fail("Accountability runtime proof requires the assigned PostgreSQL 18.6 server")
    return value


@pytest.fixture(scope="session")
def runtime_database() -> Iterator[str]:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    yield value


class _Transport:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        outcomes: tuple[Exception | None, ...] = (),
        after_start: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.sessions = sessions
        self.outcomes = list(outcomes)
        self.after_start = after_start
        self.messages: list[TelegramReplySend] = []

    async def resolve_destination(self, _session: AsyncSession, _owner_id: UUID) -> int:
        return 987654321

    async def send_reminder(self, _message: ReminderSend) -> str | None:
        raise AssertionError("runtime scenario unexpectedly sent an ordinary reminder")

    async def send_reply(self, message: TelegramReplySend) -> str | None:
        self.messages.append(message)
        if self.after_start is not None:
            callback, self.after_start = self.after_start, None
            await callback()
        if self.outcomes:
            error = self.outcomes.pop(0)
            if error is not None:
                raise error
        return f"message-{len(self.messages)}"


class _NoTodayItems:
    async def list_today_open_untimed(
        self,
        _owner_id: UUID,
        *,
        profile_zone: str,
        at: datetime,
        for_update: bool = False,
    ) -> tuple[OpenUntimedItem, ...]:
        del profile_zone, at, for_update
        return ()


async def _seed(
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID | None = None
) -> tuple[UUID, UUID, UUID]:
    owner_id = owner_id or uuid4()
    occurrence_id, plan_id = uuid4(), uuid4()
    async with sessions() as session, session.begin():
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"issue16-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="issue16-runtime-v1",
                starts_at=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=6),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language="en",
                home_zone="UTC",
                reminder_calendar_days=1,
                reminder_elapsed_hours=1,
                quiet_start=time((NOW.hour + 3) % 24, NOW.minute),
                quiet_end=time((NOW.hour + 4) % 24, NOW.minute),
                review_time=time(20),
                version=1,
            )
        )
        session.add(
            PlanItemRecord(
                id=plan_id,
                owner_id=owner_id,
                kind="appointment",
                title="Prepare report",
                version=1,
                created_at=NOW,
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
                local_date=date(2026, 10, 14),
                local_time=time(14),
                fold=None,
                end_local_date=date(2026, 10, 14),
                end_local_time=time(15),
                end_fold=None,
                starts_at=NOW - timedelta(hours=2),
                ends_at=NOW - timedelta(minutes=1),
                deadline_at=None,
                duration_seconds=3600,
                state="open",
                version=1,
                reminder_preferences_version=1,
                created_at=NOW,
            )
        )
        await session.flush()
        await synchronize_occurrence(
            session,
            CurrentOccurrence(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                occurrence_version=1,
                kind="appointment",
                state="open",
                title="Prepare report",
                event_zone="UTC",
                local_date=date(2026, 10, 14),
                starts_at=NOW - timedelta(hours=2),
                ends_at=NOW - timedelta(minutes=1),
                deadline_at=None,
            ),
            now=NOW,
        )
    return owner_id, occurrence_id, plan_id


async def _add_generic_reminder(
    session: AsyncSession,
    *,
    owner_id: UUID,
    plan_id: UUID,
    occurrence: OccurrenceRecord,
    due_at: datetime,
    enqueue: bool,
) -> ReminderIntentRecord:
    event = EventEnvelope(
        id=uuid4(),
        event_type="planning.plan_rescheduled",
        schema_version=1,
        aggregate_id=occurrence.id,
        aggregate_version=occurrence.version,
        owner_id=owner_id,
        occurred_at=NOW,
        payload={"occurrence_id": str(occurrence.id)},
    )
    await add_outbox_event(session, event)
    session.add(
        ReminderRuleRecord(
            id=uuid4(),
            owner_id=owner_id,
            occurrence_id=occurrence.id,
            offset_kind="elapsed_seconds",
            offset_amount=3600,
            source="default",
            due_at=due_at,
        )
    )
    intent = ReminderIntentRecord(
        id=uuid4(),
        owner_id=owner_id,
        plan_item_id=plan_id,
        occurrence_id=occurrence.id,
        series_id=None,
        plan_version=1,
        occurrence_version=occurrence.version,
        preferences_version=1,
        series_version=None,
        offset_kind="elapsed_seconds",
        offset_amount=3600,
        source="default",
        due_at=due_at,
        not_before_at=None,
        state="scheduled",
        dispatch_version=1,
        source_event_id=event.id,
    )
    session.add(intent)
    await session.flush()
    if enqueue:
        await enqueue_job(
            session,
            lane=JobLane.DELIVERY,
            kind="notifications.deliver_reminder",
            payload={"intent_id": str(intent.id), "dispatch_version": 1},
            deduplication_key=f"reminder:{intent.id}:1",
            owner_id=owner_id,
            priority=-100,
        )
    return intent


def _run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    run_local_async(main)


async def _claim_accountability_job(
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID, worker_id: str
) -> JobClaim:
    async with sessions() as session, session.begin():
        intent = await session.scalar(
            select(AccountabilityIntentRecord).where(
                AccountabilityIntentRecord.owner_id == owner_id,
                AccountabilityIntentRecord.slot == "initial",
            )
        )
        assert intent is not None
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                    )
                )
            ).all()
        )
        job = next(
            (
                candidate
                for candidate in jobs
                if candidate.status == "pending"
                and candidate.payload.get("intent_id") == str(intent.id)
                and candidate.payload.get("dispatch_version") == intent.dispatch_version
            ),
            None,
        )
        assert job is not None
        job.priority = 2_147_483_647
        claim = await claim_job(
            session,
            lane=JobLane.DELIVERY,
            worker_id=worker_id,
            lease_for=DELIVERY_LEASE,
        )
    assert claim is not None
    assert claim.owner_id == owner_id
    assert claim.payload.get("intent_id") == str(intent.id)
    return claim


async def _prioritize_owner_initial_job(
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID
) -> None:
    """Keep runtime assertions focused on this scenario in the shared delivery lane."""
    async with sessions() as session, session.begin():
        await schedule_due_accountability(
            session,
            profiles=(_profile(owner_id),),
            today_untimed=_NoTodayItems(),
            now=NOW,
        )
        intent = await session.scalar(
            select(AccountabilityIntentRecord).where(
                AccountabilityIntentRecord.owner_id == owner_id,
                AccountabilityIntentRecord.slot == "initial",
            )
        )
        assert intent is not None
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                    )
                )
            ).all()
        )
        job = next(
            (
                candidate
                for candidate in jobs
                if candidate.status == "pending"
                and candidate.payload.get("intent_id") == str(intent.id)
                and candidate.payload.get("dispatch_version") == intent.dispatch_version
            ),
            None,
        )
        assert job is not None
        job.priority = 2_147_483_647


async def _prioritize_delivery_intent_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    owner_id: UUID,
    intent_id: UUID,
    kind: str,
    dispatch_version: int | None = None,
) -> None:
    async with sessions() as session, session.begin():
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.lane == JobLane.DELIVERY.value,
                        Job.kind == kind,
                        Job.status == "pending",
                    )
                )
            ).all()
        )
        job = next(
            (
                candidate
                for candidate in jobs
                if candidate.payload.get("intent_id") == str(intent_id)
                and (
                    dispatch_version is None
                    or candidate.payload.get("dispatch_version") == dispatch_version
                )
            ),
            None,
        )
        assert job is not None
        job.priority = 2_147_483_647


async def _retire_own_accountability_job(
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID
) -> None:
    """Retire this test owner's synthetic jobs after its assertions complete."""
    async with sessions() as session, session.begin():
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                    )
                )
            ).all()
        )
        for job in jobs:
            assert job.status in {"pending", "succeeded"}
            job.status = "succeeded"
            job.finished_at = NOW
            job.worker_id = None
            job.lease_token = None
            job.lease_until = None

        # Later tests call the real global scheduler at later instants. Close only
        # this scenario's synthetic subjects after assertions have proved their
        # open-cycle/review behavior, preventing a later test from creating a
        # nudge for an earlier test owner.
        cycles = list(
            (
                await session.scalars(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.state == "open",
                    )
                )
            ).all()
        )
        reviews = list(
            (
                await session.scalars(
                    select(DailyReviewRecord).where(
                        DailyReviewRecord.owner_id == owner_id,
                        DailyReviewRecord.state == "open",
                    )
                )
            ).all()
        )
        for cycle in cycles:
            cycle.state = "cancelled"
            cycle.nudge_slot_used = True
        for review in reviews:
            review.state = "cancelled"
            review.nudge_slot_used = True


def _profile(owner_id: UUID) -> ReviewProfile:
    return ReviewProfile(
        owner_id=owner_id,
        home_zone="UTC",
        language="en",
        review_time=time(20),
        quiet_start=time((NOW.hour + 3) % 24, NOW.minute),
        quiet_end=time((NOW.hour + 4) % 24, NOW.minute),
        destination_id=987654321,
    )


def test_delivery_runtime_persists_attempt_event_and_uses_confirmed_instant(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        transport = _Transport(
            sessions,
            outcomes=(KnownDeliveryFailure("busy", retry_after=timedelta(0)), None),
        )
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            retry_at = NOW
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: retry_at
            )
            confirmed_at = retry_at
            assert len(transport.messages) == 2

            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord)
                            .join(
                                AccountabilityIntentRecord,
                                AccountabilityIntentRecord.id
                                == AccountabilityDeliveryAttemptRecord.intent_id,
                            )
                            .where(AccountabilityIntentRecord.owner_id == owner_id)
                            .order_by(AccountabilityDeliveryAttemptRecord.attempt_number)
                        )
                    ).all()
                )
                events = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.owner_id == owner_id,
                                OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                            )
                        )
                    ).all()
                )
                assert cycle is not None
                assert [attempt.outcome for attempt in attempts] == ["failed", "confirmed"]
                event_outcomes = sorted(
                    (
                        int(event.payload["payload"]["attempt_number"]),
                        event.payload["payload"]["outcome"],
                    )
                    for event in events
                )
                assert event_outcomes == [(1, "failed"), (2, "confirmed")]
                assert all(
                    event.event_type == "notifications.delivery_attempt_outcome" for event in events
                )

            before_due = confirmed_at + timedelta(minutes=59)
            assert not await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: before_due
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None
                assert cycle.initial_confirmed_at == confirmed_at
                assert cycle.nudge_due_at == confirmed_at + timedelta(hours=1)
            nudge_due = confirmed_at + timedelta(hours=1)
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: nudge_due
            )
            assert len(transport.messages) == 3
            async with sessions() as session:
                intents = list(
                    (
                        await session.scalars(
                            select(AccountabilityIntentRecord).where(
                                AccountabilityIntentRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert sum(intent.slot == "nudge" for intent in intents) == 1
                assert sum(intent.slot == "initial" for intent in intents) == 1
        finally:
            await engine.dispose()

    _run(scenario)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (UnknownDeliveryFailure("provider_timeout"), DeliveryOutcomeKind.UNKNOWN.value),
        (KnownDeliveryFailure("rejected"), DeliveryOutcomeKind.FAILED.value),
    ],
)
def test_failed_or_unknown_runtime_send_never_starts_a_nudge_timer(
    runtime_database: str, failure: Exception, expected: str
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        transport = _Transport(sessions, outcomes=(failure,))
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            later = NOW + timedelta(hours=2)
            await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: later
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                intents = list(
                    (
                        await session.scalars(
                            select(AccountabilityIntentRecord).where(
                                AccountabilityIntentRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert cycle is not None
                assert cycle.initial_confirmed_at is None
                assert cycle.nudge_due_at is None
                assert [attempt.outcome for attempt in attempts] == [expected]
                assert all(intent.slot == "initial" for intent in intents)
        finally:
            await engine.dispose()

    _run(scenario)


def test_nudge_authorization_uses_persisted_appointment_due_instant(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        expected_due = NOW + timedelta(hours=1)
        try:
            async with sessions() as session, session.begin():
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None
                cycle.initial_confirmed_at = NOW
                cycle.nudge_due_at = expected_due
                intent = AccountabilityIntentRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    cycle_id=cycle.id,
                    review_id=None,
                    slot="nudge",
                    due_at=NOW,
                    state="scheduled",
                    attempt_count=0,
                    last_outcome_attempt=0,
                )
                session.add(intent)
                await session.flush()
                authorization = await authorize_accountability_send(
                    session,
                    intent_id=intent.id,
                    profile=_profile(owner_id),
                    current_occurrences=PlanningAccountabilityReads(session),
                    today_untimed=_NoTodayItems(),
                    now=NOW + timedelta(minutes=5),
                )
                assert authorization.send is None
                assert authorization.reason == "nudge_not_due"
                assert authorization.deferred_until == expected_due
                assert intent.state == "scheduled" and intent.attempt_count == 0
                cycle.state = "completed"
        finally:
            await engine.dispose()

    _run(scenario)


def test_review_nudge_without_initial_confirmation_starts_no_attempt(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, _occurrence_id, _plan_id = await _seed(sessions)
        profile = _profile(owner_id)
        try:
            async with sessions() as session, session.begin():
                review = DailyReviewRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    review_date=NOW.date(),
                    zone_name=profile.home_zone,
                    language=profile.language,
                    destination_id=profile.destination_id,
                    occurrence_ids=[],
                    state="open",
                    due_at=NOW - timedelta(hours=1),
                    initial_confirmed_at=None,
                    nudge_due_at=NOW + timedelta(hours=1),
                    nudge_slot_used=True,
                )
                session.add(review)
                await session.flush()
                intent = AccountabilityIntentRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    cycle_id=None,
                    review_id=review.id,
                    slot="nudge",
                    due_at=NOW,
                    state="scheduled",
                    attempt_count=0,
                    last_outcome_attempt=0,
                )
                session.add(intent)
                await session.flush()
                authorization = await authorize_accountability_send(
                    session,
                    intent_id=intent.id,
                    profile=profile,
                    current_occurrences=PlanningAccountabilityReads(session),
                    today_untimed=_NoTodayItems(),
                    now=NOW,
                )
                assert authorization.send is None
                assert authorization.reason == "initial_send_not_confirmed"
                assert intent.state == "skipped" and intent.attempt_count == 0
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(CheckInCycleRecord.owner_id == owner_id)
                )
                assert cycle is not None
                cycle.state = "completed"
        finally:
            await engine.dispose()

    _run(scenario)


def test_review_nudge_in_quiet_hours_defers_without_starting_attempt(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, _occurrence_id, _plan_id = await _seed(sessions)
        profile = ReviewProfile(
            owner_id=owner_id,
            home_zone="UTC",
            language="en",
            review_time=time(20),
            quiet_start=time((NOW.hour - 1) % 24),
            quiet_end=time((NOW.hour + 1) % 24),
            destination_id=987654321,
        )
        try:
            async with sessions() as session, session.begin():
                review = DailyReviewRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    review_date=NOW.date(),
                    zone_name=profile.home_zone,
                    language=profile.language,
                    destination_id=profile.destination_id,
                    occurrence_ids=[],
                    state="open",
                    due_at=NOW - timedelta(hours=2),
                    initial_confirmed_at=NOW - timedelta(hours=2),
                    nudge_due_at=NOW - timedelta(hours=1),
                    nudge_slot_used=True,
                )
                session.add(review)
                await session.flush()
                intent = AccountabilityIntentRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    cycle_id=None,
                    review_id=review.id,
                    slot="nudge",
                    due_at=NOW - timedelta(hours=1),
                    state="scheduled",
                    attempt_count=0,
                    last_outcome_attempt=0,
                )
                session.add(intent)
                await session.flush()
                authorization = await authorize_accountability_send(
                    session,
                    intent_id=intent.id,
                    profile=profile,
                    current_occurrences=PlanningAccountabilityReads(session),
                    today_untimed=_NoTodayItems(),
                    now=NOW,
                )
                assert authorization.send is None
                assert authorization.reason == "quiet_hours"
                assert authorization.deferred_until is not None
                assert authorization.deferred_until > NOW
                assert intent.attempt_count == 0
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(CheckInCycleRecord.owner_id == owner_id)
                )
                assert cycle is not None
                cycle.state = "completed"
        finally:
            await engine.dispose()

    _run(scenario)


def test_account_block_suppresses_a_later_accountability_intent(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        transport = _Transport(
            sessions,
            outcomes=(KnownDeliveryFailure("destination_forbidden", blocked=True),),
        )
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert len(transport.messages) == 1
            async with sessions() as session, session.begin():
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None
                occurrence.version = 2
                occurrence.local_time = (NOW - timedelta(hours=2)).time().replace(tzinfo=None)
                occurrence.end_local_time = (NOW - timedelta(minutes=1)).time().replace(tzinfo=None)
                occurrence.starts_at = NOW - timedelta(hours=2)
                occurrence.ends_at = NOW - timedelta(minutes=1)
                await synchronize_occurrence(
                    session,
                    CurrentOccurrence(
                        owner_id=owner_id,
                        occurrence_id=occurrence_id,
                        occurrence_version=2,
                        kind="appointment",
                        state="open",
                        title="Prepare report",
                        event_zone="UTC",
                        local_date=NOW.date(),
                        starts_at=occurrence.starts_at,
                        ends_at=occurrence.ends_at,
                        deadline_at=None,
                    ),
                    now=NOW,
                )

            async with sessions() as session, session.begin():
                await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=_NoTodayItems(),
                    now=NOW + timedelta(minutes=1),
                )

            async with sessions() as session:
                next_cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                        CheckInCycleRecord.occurrence_version == 2,
                    )
                )
                assert next_cycle is not None
                next_intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.cycle_id == next_cycle.id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                assert next_intent is not None
                next_intent_id = next_intent.id
            await _prioritize_delivery_intent_job(
                sessions,
                owner_id=owner_id,
                intent_id=next_intent_id,
                kind=ACCOUNTABILITY_DELIVERY_JOB_KIND,
                dispatch_version=next_intent.dispatch_version,
            )
            assert await process_delivery_iteration(
                sessions,
                transport,
                worker_id="accountability-runtime",
                now=lambda: NOW + timedelta(minutes=1),
            )
            assert len(transport.messages) == 1
            async with sessions() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                cycles = list(
                    (
                        await session.scalars(
                            select(CheckInCycleRecord)
                            .where(
                                CheckInCycleRecord.owner_id == owner_id,
                                CheckInCycleRecord.occurrence_id == occurrence_id,
                            )
                            .order_by(CheckInCycleRecord.occurrence_version)
                        )
                    ).all()
                )
                intents = list(
                    (
                        await session.scalars(
                            select(AccountabilityIntentRecord).where(
                                AccountabilityIntentRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert [attempt.outcome for attempt in attempts] == ["blocked"]
                assert len(cycles) == 2 and cycles[1].state == "open"
                second_intent = next(
                    intent for intent in intents if intent.cycle_id == cycles[1].id
                )
                assert second_intent.state == "skipped" and second_intent.attempt_count == 0
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_account_block_suppresses_a_generic_reminder(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        transport = _Transport(
            sessions,
            outcomes=(KnownDeliveryFailure("destination_forbidden", blocked=True),),
        )
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert len(transport.messages) == 1
            async with sessions() as session, session.begin():
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert cycle is not None and occurrence is not None
                cycle.state = "completed"
                intent = await _add_generic_reminder(
                    session,
                    owner_id=owner_id,
                    plan_id=plan_id,
                    occurrence=occurrence,
                    due_at=NOW - timedelta(minutes=5),
                    enqueue=True,
                )

            await _prioritize_delivery_intent_job(
                sessions,
                owner_id=owner_id,
                intent_id=intent.id,
                kind="notifications.deliver_reminder",
            )
            assert await process_delivery_iteration(
                sessions,
                transport,
                worker_id="accountability-runtime",
                now=lambda: NOW + timedelta(minutes=1),
            )
            assert len(transport.messages) == 1
            async with sessions() as session:
                generic_attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == intent.id
                    )
                )
                account_attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert generic_attempt is not None
                assert generic_attempt.outcome == "blocked"
                assert generic_attempt.failure_code == "destination_previously_blocked"
                assert len(account_attempts) == 1
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_generic_block_suppresses_a_later_accountability_send(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        transport = _Transport(sessions)
        try:
            async with sessions() as session, session.begin():
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None
                generic_intent = await _add_generic_reminder(
                    session,
                    owner_id=owner_id,
                    plan_id=plan_id,
                    occurrence=occurrence,
                    due_at=NOW + timedelta(days=1),
                    enqueue=False,
                )
                session.add(
                    DeliveryAttemptRecord(
                        id=uuid4(),
                        owner_id=owner_id,
                        intent_id=generic_intent.id,
                        reply_event_id=None,
                        recovery_episode_key=None,
                        target_id=987654321,
                        dispatch_version=1,
                        attempt_number=1,
                        check_in_cycle_id=None,
                        access_period_id=f"issue16-{owner_id}",
                        outcome="blocked",
                        failure_code="destination_forbidden",
                        started_at=NOW - timedelta(minutes=1),
                        finished_at=NOW,
                    )
                )

            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert transport.messages == []
            async with sessions() as session:
                account_attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                initial_intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.owner_id == owner_id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                assert account_attempts == []
                assert initial_intent is not None and initial_intent.state == "skipped"
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_preferences_changed_after_access_fence_control_quiet_authorization(
    runtime_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, _occurrence_id, _plan_id = await _seed(sessions)
        transport = _Transport(sessions)

        async def save_quiet_preferences() -> None:
            async with sessions() as session, session.begin():
                identity = SqlAlchemyIdentityStore(session)
                current = await identity.get_preferences(owner_id)
                assert current is not None
                updated = await identity.save_preferences(
                    owner_id=owner_id,
                    expected_version=current.version,
                    preferences=Preferences(
                        language=current.language,
                        home_zone=current.home_zone,
                        reminder_calendar_days=current.reminder_calendar_days,
                        reminder_elapsed_hours=current.reminder_elapsed_hours,
                        quiet_start=time((NOW.hour - 1) % 24, NOW.minute),
                        quiet_end=time((NOW.hour + 1) % 24, NOW.minute),
                        review_time=current.review_time,
                        version=current.version + 1,
                    ),
                )
                assert updated is not None

        original_lock = SqlAlchemyAccessUsageStore.lock_access
        changed = False

        async def change_preferences_after_access_fence(
            store: SqlAlchemyAccessUsageStore, check: CheckAccess
        ) -> AccessDecision:
            nonlocal changed
            access = await original_lock(store, check)
            if not changed:
                changed = True
                await save_quiet_preferences()
            return access

        monkeypatch.setattr(
            SqlAlchemyAccessUsageStore, "lock_access", change_preferences_after_access_fence
        )
        try:
            async with sessions() as session, session.begin():
                await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=PlanningAccountabilityReads(session),
                    now=NOW,
                )
            claim = await _claim_accountability_job(
                sessions, owner_id=owner_id, worker_id="accountability-pref-race"
            )

            async def profile_reader(
                session: AsyncSession, read_owner: UUID
            ) -> ReviewProfile | None:
                return await read_review_profile(session, read_owner, transport)

            outcome = await deliver_accountability_job(
                sessions,
                claim,
                transport,
                profile_reader=profile_reader,
                current_occurrences_factory=PlanningAccountabilityReads,
                today_untimed_factory=PlanningAccountabilityReads,
                render=render_accountability_message,
                now=lambda: NOW,
            )
            assert outcome is DeliveryOutcomeKind.DEFERRED
            assert changed
            assert transport.messages == []
            async with sessions() as session:
                attempt = await session.scalar(
                    select(AccountabilityDeliveryAttemptRecord).where(
                        AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                    )
                )
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(CheckInCycleRecord.owner_id == owner_id)
                )
                preferences = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
                assert attempt is None
                assert cycle is not None and cycle.initial_confirmed_at is None
                assert preferences is not None and preferences.version == 2
                assert preferences.quiet_start <= time(NOW.hour, NOW.minute) or (
                    preferences.quiet_start > preferences.quiet_end
                    and time(NOW.hour, NOW.minute) < preferences.quiet_end
                )
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_preferences_changed_after_profile_fence_wait_for_attempt_start(
    runtime_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        transport = _Transport(sessions)
        profile_reader_entered = asyncio.Event()
        profile_locked = asyncio.Event()
        release_profile_reader = asyncio.Event()

        async def paused_profile_reader(
            session: AsyncSession, read_owner: UUID
        ) -> ReviewProfile | None:
            profile_reader_entered.set()
            profile = await read_review_profile(session, read_owner, transport)
            profile_locked.set()
            await release_profile_reader.wait()
            return profile

        monkeypatch.setattr(telegram_delivery_worker, "read_review_profile", paused_profile_reader)

        async def save_quiet_preferences() -> None:
            async with sessions() as session, session.begin():
                identity = SqlAlchemyIdentityStore(session)
                current = await identity.get_preferences(owner_id)
                assert current is not None
                updated = await identity.save_preferences(
                    owner_id=owner_id,
                    expected_version=current.version,
                    preferences=Preferences(
                        language=current.language,
                        home_zone=current.home_zone,
                        reminder_calendar_days=current.reminder_calendar_days,
                        reminder_elapsed_hours=current.reminder_elapsed_hours,
                        quiet_start=time((NOW.hour - 1) % 24, NOW.minute),
                        quiet_end=time((NOW.hour + 1) % 24, NOW.minute),
                        review_time=current.review_time,
                        version=current.version + 1,
                    ),
                )
                assert updated is not None

        delivery_task: asyncio.Task[DeliveryOutcomeKind | None] | None = None
        update_task: asyncio.Task[None] | None = None
        try:
            async with sessions() as session, session.begin():
                await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=PlanningAccountabilityReads(session),
                    now=NOW,
                )

            claim = await _claim_accountability_job(
                sessions, owner_id=owner_id, worker_id="accountability-pref-race"
            )
            delivery_task = asyncio.create_task(
                deliver_accountability_job(
                    sessions,
                    claim,
                    transport,
                    profile_reader=paused_profile_reader,
                    current_occurrences_factory=PlanningAccountabilityReads,
                    today_untimed_factory=PlanningAccountabilityReads,
                    render=render_accountability_message,
                    now=lambda: NOW,
                )
            )
            await asyncio.wait_for(profile_reader_entered.wait(), timeout=5)
            await asyncio.wait_for(profile_locked.wait(), timeout=5)
            update_task = asyncio.create_task(save_quiet_preferences())
            await asyncio.sleep(0.1)
            assert not update_task.done(), "the preference writer crossed the locked profile"
            release_profile_reader.set()
            assert await delivery_task is DeliveryOutcomeKind.CONFIRMED
            await update_task
            assert len(transport.messages) == 1
            async with sessions() as session, session.begin():
                await consume_delivery_outcomes(session, at=NOW)
            async with sessions() as session:
                attempt = await session.scalar(
                    select(AccountabilityDeliveryAttemptRecord).where(
                        AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                    )
                )
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                preferences = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
                assert attempt is not None and attempt.outcome == "confirmed"
                assert cycle is not None and cycle.initial_confirmed_at == NOW
                assert cycle.nudge_due_at == NOW + timedelta(hours=1)
                assert preferences is not None and preferences.version == 2
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            release_profile_reader.set()
            if delivery_task is not None and not delivery_task.done():
                await delivery_task
            if update_task is not None and not update_task.done():
                await update_task
            await engine.dispose()

    _run(scenario)


def test_scheduler_cycle_lock_precedes_intent_lock_against_send_authorization(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        async with sessions() as session, session.begin():
            await schedule_due_accountability(
                session,
                profiles=(_profile(owner_id),),
                today_untimed=PlanningAccountabilityReads(session),
                now=NOW,
            )
        cycle_locked = asyncio.Event()
        authorization_started = asyncio.Event()
        scheduler_pid: int | None = None
        authorization_pid: int | None = None

        async def schedule_while_holding_cycle() -> int:
            nonlocal scheduler_pid
            async with sessions() as session, session.begin():
                scheduler_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                cycle = await session.scalar(
                    select(CheckInCycleRecord)
                    .where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                    .with_for_update()
                )
                assert cycle is not None
                cycle_locked.set()
                await authorization_started.wait()
                deadline = asyncio.get_running_loop().time() + 3
                authorization_blocked_by_scheduler = False
                while asyncio.get_running_loop().time() < deadline:
                    async with sessions() as monitor:
                        authorization_blocked_by_scheduler = bool(
                            await monitor.scalar(
                                text(
                                    "SELECT :scheduler_pid = ANY "
                                    "(pg_blocking_pids(:authorization_pid))"
                                ),
                                {
                                    "scheduler_pid": scheduler_pid,
                                    "authorization_pid": authorization_pid,
                                },
                            )
                        )
                    if authorization_blocked_by_scheduler:
                        break
                    await asyncio.sleep(0.05)
                assert authorization_blocked_by_scheduler
                return await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=PlanningAccountabilityReads(session),
                    now=NOW,
                )

        async def authorize_after_scheduler_has_cycle() -> None:
            nonlocal authorization_pid
            await cycle_locked.wait()
            async with sessions() as session, session.begin():
                intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.owner_id == owner_id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                assert intent is not None
                authorization_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                authorization_started.set()
                result = await authorize_accountability_send(
                    session,
                    intent_id=intent.id,
                    profile=_profile(owner_id),
                    current_occurrences=PlanningAccountabilityReads(session),
                    today_untimed=PlanningAccountabilityReads(session),
                    now=NOW,
                )
                assert result.send is not None

        scheduler_task = asyncio.create_task(schedule_while_holding_cycle())
        await asyncio.wait_for(cycle_locked.wait(), timeout=5)
        authorization_task = asyncio.create_task(authorize_after_scheduler_has_cycle())
        try:
            assert await asyncio.wait_for(scheduler_task, timeout=5) == 0
            await asyncio.wait_for(authorization_task, timeout=5)
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            for task in (scheduler_task, authorization_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(scheduler_task, authorization_task, return_exceptions=True)
            await engine.dispose()

    _run(scenario)


def test_delivery_outcome_cycle_lock_precedes_intent_lock_against_scheduler(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        async with sessions() as session, session.begin():
            await schedule_due_accountability(
                session,
                profiles=(_profile(owner_id),),
                today_untimed=PlanningAccountabilityReads(session),
                now=NOW,
            )
            intent = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.owner_id == owner_id,
                    AccountabilityIntentRecord.slot == "initial",
                )
            )
            assert intent is not None
            intent_id = intent.id

        cycle_locked = asyncio.Event()
        outcome_started = asyncio.Event()
        scheduler_pid: int | None = None
        outcome_pid: int | None = None

        async def schedule_while_holding_cycle() -> int:
            nonlocal scheduler_pid
            async with sessions() as session, session.begin():
                scheduler_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                cycle = await session.scalar(
                    select(CheckInCycleRecord)
                    .where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                    .with_for_update()
                )
                assert cycle is not None
                cycle_locked.set()
                await outcome_started.wait()
                deadline = asyncio.get_running_loop().time() + 3
                outcome_blocked_by_scheduler = False
                while asyncio.get_running_loop().time() < deadline:
                    async with sessions() as monitor:
                        outcome_blocked_by_scheduler = bool(
                            await monitor.scalar(
                                text(
                                    "SELECT :scheduler_pid = ANY (pg_blocking_pids(:outcome_pid))"
                                ),
                                {"scheduler_pid": scheduler_pid, "outcome_pid": outcome_pid},
                            )
                        )
                    if outcome_blocked_by_scheduler:
                        break
                    await asyncio.sleep(0.05)
                assert outcome_blocked_by_scheduler
                return await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=PlanningAccountabilityReads(session),
                    now=NOW,
                )

        async def apply_outcome_after_scheduler_has_cycle() -> None:
            nonlocal outcome_pid
            await cycle_locked.wait()
            async with sessions() as session, session.begin():
                outcome_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                outcome_started.set()
                assert await apply_delivery_outcome(
                    session,
                    event_id=uuid4(),
                    owner_id=owner_id,
                    intent_id=intent_id,
                    outcome="confirmed",
                    occurred_at=NOW,
                    attempt_number=1,
                )

        scheduler_task = asyncio.create_task(schedule_while_holding_cycle())
        outcome_task = asyncio.create_task(apply_outcome_after_scheduler_has_cycle())
        try:
            assert await asyncio.wait_for(scheduler_task, timeout=6) == 0
            await asyncio.wait_for(outcome_task, timeout=6)
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(CheckInCycleRecord.owner_id == owner_id)
                )
                intent = await session.get(AccountabilityIntentRecord, intent_id)
                assert cycle is not None and cycle.initial_confirmed_at == NOW
                assert cycle.nudge_due_at == NOW + timedelta(hours=1)
                assert intent is not None and intent.state == "confirmed"
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            for task in (scheduler_task, outcome_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(scheduler_task, outcome_task, return_exceptions=True)
            await engine.dispose()

    _run(scenario)


def test_terminal_mutation_after_durable_attempt_start_does_not_restart_cycle(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)

        async def complete_after_start() -> None:
            async with sessions() as session, session.begin():
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None
                occurrence.state = "completed"
                occurrence.version += 1
                await synchronize_occurrence(
                    session,
                    CurrentOccurrence(
                        owner_id=owner_id,
                        occurrence_id=occurrence_id,
                        occurrence_version=occurrence.version,
                        kind="appointment",
                        state="completed",
                        title="Prepare report",
                        event_zone="UTC",
                        local_date=occurrence.local_date,
                        starts_at=occurrence.starts_at,
                        ends_at=occurrence.ends_at,
                        deadline_at=None,
                    ),
                    now=NOW,
                )

        transport = _Transport(sessions, after_start=complete_after_start)
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert len(transport.messages) == 1
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.state == "completed"

            await process_delivery_iteration(
                sessions,
                transport,
                worker_id="accountability-runtime",
                now=lambda: NOW + timedelta(hours=2),
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.state == "completed"
                assert cycle.initial_confirmed_at is None
                assert cycle.nudge_due_at is None
                assert (
                    await session.scalar(
                        select(AccountabilityOutcomeReceiptRecord.id).where(
                            AccountabilityOutcomeReceiptRecord.owner_id == owner_id
                        )
                    )
                    is not None
                )
        finally:
            await engine.dispose()

    _run(scenario)


def test_accountability_callback_previews_then_confirmation_closes_cycle(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        transport = _Transport(sessions)
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.initial_confirmed_at is not None
                confirmed_at = cycle.initial_confirmed_at
                initial_intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.cycle_id == cycle.id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                assert initial_intent is not None and initial_intent.state == "confirmed"
                attempts = list(
                    await session.scalars(
                        select(AccountabilityDeliveryAttemptRecord).where(
                            AccountabilityDeliveryAttemptRecord.intent_id == initial_intent.id
                        )
                    )
                )
                assert len(attempts) == 1
                assert attempts[0].outcome == "confirmed"
                assert attempts[0].finished_at == confirmed_at
            assert len(transport.messages) == 1
            keyboard = transport.messages[0].reply_markup
            assert keyboard is not None
            rows = cast(list[list[dict[str, str]]], keyboard["inline_keyboard"])
            callback = rows[0][0]["callback_data"]
            assert callback.startswith("ac:")

            async with sessions() as session, session.begin():
                preview = await process_accountability_action(
                    session,
                    owner_id=owner_id,
                    data=callback,
                    language="en",
                    now=NOW + timedelta(minutes=1),
                )
                assert (
                    preview is not None and "mark this occurrence done" in preview.text.casefold()
                )
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "open"
                assert preview.keyboard is not None
                preview_rows = cast(list[list[dict[str, str]]], preview.keyboard["inline_keyboard"])
                confirm_callback = preview_rows[0][0]["callback_data"]
                assert confirm_callback.startswith(MUTATION_CALLBACK_PREFIX)

            async with sessions() as session, session.begin():
                confirmed = await process_mutation_callback(
                    session,
                    owner_id=owner_id,
                    data=confirm_callback,
                    language="en",
                    now=NOW + timedelta(minutes=2),
                )
                assert "done" in confirmed.text.casefold()
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "completed"

            async with sessions() as session, session.begin():
                completed_events = list(
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.owner_id == owner_id,
                            OutboxEvent.aggregate_id == occurrence_id,
                            OutboxEvent.event_type == "planning.occurrence_completed",
                        )
                    )
                )
                assert len(completed_events) == 1
                completed_event_id = completed_events[0].id
                processed = await consume_planning_cycle_events(
                    session,
                    current_occurrences=PlanningAccountabilityReads(session),
                    at=NOW + timedelta(minutes=2),
                )
                assert processed >= 1
                receipt = await session.scalar(
                    select(AccountabilityOutcomeReceiptRecord).where(
                        AccountabilityOutcomeReceiptRecord.owner_id == owner_id,
                        AccountabilityOutcomeReceiptRecord.event_id == completed_event_id,
                    )
                )
                assert receipt is not None

            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.state == "completed"
                assert cycle.initial_confirmed_at == confirmed_at
                intents = list(
                    await session.scalars(
                        select(AccountabilityIntentRecord).where(
                            AccountabilityIntentRecord.cycle_id == cycle.id
                        )
                    )
                )
                assert len(intents) == 1
                assert intents[0].slot == "initial" and intents[0].state == "confirmed"
                attempts = list(
                    await session.scalars(
                        select(AccountabilityDeliveryAttemptRecord).where(
                            AccountabilityDeliveryAttemptRecord.intent_id == intents[0].id
                        )
                    )
                )
                assert len(attempts) == 1
                assert attempts[0].outcome == "confirmed"
                assert attempts[0].finished_at == confirmed_at
                pending_jobs = list(
                    await session.scalars(
                        select(Job).where(
                            Job.owner_id == owner_id,
                            Job.lane == JobLane.DELIVERY.value,
                            Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                            Job.status.in_(("pending", "leased")),
                        )
                    )
                )
                assert pending_jobs == []
            assert len(transport.messages) == 1
        finally:
            await engine.dispose()

    _run(scenario)


def test_runtime_defers_an_initial_check_until_quiet_hours_end(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        quiet_start = time((NOW.hour - 1) % 24, NOW.minute)
        quiet_end = time((NOW.hour + 1) % 24, NOW.minute)
        async with sessions() as session, session.begin():
            profile = await session.get(PreferencesRecord, owner_id)
            assert profile is not None
            profile.quiet_start = quiet_start
            profile.quiet_end = quiet_end
        transport = _Transport(sessions)
        try:
            await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert transport.messages == []
            local_end = datetime.combine(NOW.date(), quiet_end, tzinfo=UTC)
            if quiet_start > quiet_end:
                local_end += timedelta(days=1)
            async with sessions() as session:
                intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.owner_id == owner_id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                assert intent is not None and intent.due_at == local_end
                assert (
                    await session.scalar(
                        select(AccountabilityDeliveryAttemptRecord.id).where(
                            AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.initial_confirmed_at is None
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_expired_entitlement_skips_send_and_renewal_does_not_revive_exhausted_cycle(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        transport = _Transport(sessions)
        try:
            async with sessions() as session, session.begin():
                expired = await session.scalar(
                    select(AccessPeriodRecord).where(AccessPeriodRecord.owner_id == owner_id)
                )
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert expired is not None and cycle is not None
                expired.ends_at = NOW - timedelta(seconds=1)

            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert transport.messages == []
            async with sessions() as session, session.begin():
                cycle = await session.get(CheckInCycleRecord, cycle.id)
                assert cycle is not None
                cycle.state = "exhausted"
                cycle.nudge_slot_used = True
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=f"renewed-{uuid4().hex}",
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="issue16-renewed-v1",
                        starts_at=NOW,
                        ends_at=NOW + timedelta(days=30),
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )

            async with sessions() as session, session.begin():
                await schedule_due_accountability(
                    session,
                    profiles=(_profile(owner_id),),
                    today_untimed=_NoTodayItems(),
                    now=NOW + timedelta(minutes=1),
                )
            async with sessions() as session:
                cycle = await session.get(CheckInCycleRecord, cycle.id)
                intents = list(
                    (
                        await session.scalars(
                            select(AccountabilityIntentRecord).where(
                                AccountabilityIntentRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert cycle is not None and cycle.state == "exhausted"
                assert len(intents) == 1 and intents[0].state == "skipped"
                assert attempts == []
                assert transport.messages == []
                future_jobs = list(
                    await session.scalars(
                        select(Job).where(
                            Job.owner_id == owner_id,
                            Job.lane == JobLane.DELIVERY.value,
                            Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                            Job.status.in_(("pending", "leased")),
                        )
                    )
                )
                assert future_jobs == []
            await _retire_own_accountability_job(sessions, owner_id=owner_id)
        finally:
            await engine.dispose()

    _run(scenario)


def test_expired_entitlement_reconciles_reclaimed_started_attempt_as_unknown(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        async with sessions() as session, session.begin():
            await schedule_due_accountability(
                session,
                profiles=(_profile(owner_id),),
                today_untimed=PlanningAccountabilityReads(session),
                now=NOW,
            )
        claim = await _claim_accountability_job(
            sessions, owner_id=owner_id, worker_id="accountability-expired-reclaim"
        )
        async with sessions() as session, session.begin():
            intent = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.owner_id == owner_id,
                    AccountabilityIntentRecord.slot == "initial",
                )
            )
            period = await session.scalar(
                select(AccessPeriodRecord).where(AccessPeriodRecord.owner_id == owner_id)
            )
            assert intent is not None and period is not None
            intent.state = "sending"
            period.ends_at = NOW - timedelta(seconds=1)
            session.add(
                AccountabilityDeliveryAttemptRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    intent_id=intent.id,
                    target_id=987654321,
                    attempt_number=1,
                    outcome="started",
                    access_period_id=period.period_id,
                    started_at=NOW - timedelta(minutes=5),
                )
            )
        transport = _Transport(sessions)

        async def profile_must_not_be_read(
            _session: AsyncSession, _owner_id: UUID
        ) -> ReviewProfile:
            raise AssertionError("reclaimed external attempt must reconcile before authorization")

        try:
            outcome = await deliver_accountability_job(
                sessions,
                claim,
                transport,
                profile_reader=profile_must_not_be_read,
                current_occurrences_factory=PlanningAccountabilityReads,
                today_untimed_factory=PlanningAccountabilityReads,
                render=render_accountability_message,
                now=lambda: NOW,
            )
            assert outcome is DeliveryOutcomeKind.UNKNOWN
            assert transport.messages == []
            async with sessions() as session, session.begin():
                assert await consume_delivery_outcomes(session, at=NOW) == 1
            async with sessions() as session:
                attempt = await session.scalar(
                    select(AccountabilityDeliveryAttemptRecord).where(
                        AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                    )
                )
                intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.owner_id == owner_id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                events = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.owner_id == owner_id,
                                OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                            )
                        )
                    ).all()
                )
                assert attempt is not None and attempt.outcome == "unknown"
                assert attempt.failure_code == "worker_lost_after_send_started"
                assert attempt.finished_at == NOW
                assert intent is not None and intent.state == "unknown"
                assert cycle is not None and cycle.initial_confirmed_at is None
                assert cycle.nudge_due_at is None
                assert len(events) == 1
                assert events[0].payload["payload"]["outcome"] == "unknown"
                assert events[0].payload["payload"]["attempt_number"] == 1
        finally:
            await engine.dispose()

    _run(scenario)


def test_reschedule_event_supersedes_due_cycle_before_attempt_start(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        async with sessions() as session, session.begin():
            await schedule_due_accountability(
                session,
                profiles=(_profile(owner_id),),
                today_untimed=_NoTodayItems(),
                now=NOW,
            )
            occurrence = await session.get(OccurrenceRecord, occurrence_id)
            assert occurrence is not None
            occurrence.local_time = (NOW + timedelta(hours=2)).time().replace(tzinfo=None)
            occurrence.end_local_time = (NOW + timedelta(hours=3)).time().replace(tzinfo=None)
            occurrence.starts_at = NOW + timedelta(hours=2)
            occurrence.ends_at = NOW + timedelta(hours=3)
            occurrence.version += 1
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.plan_rescheduled",
                    schema_version=1,
                    aggregate_id=occurrence_id,
                    aggregate_version=occurrence.version,
                    owner_id=owner_id,
                    occurred_at=NOW,
                    payload={"occurrence_id": str(occurrence_id)},
                ),
            )

        transport = _Transport(sessions)
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert transport.messages == []
            async with sessions() as session:
                cycles = list(
                    (
                        await session.scalars(
                            select(CheckInCycleRecord)
                            .where(
                                CheckInCycleRecord.owner_id == owner_id,
                                CheckInCycleRecord.occurrence_id == occurrence_id,
                            )
                            .order_by(CheckInCycleRecord.occurrence_version)
                        )
                    ).all()
                )
                intents = list(
                    (
                        await session.scalars(
                            select(AccountabilityIntentRecord).where(
                                AccountabilityIntentRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert len(cycles) == 2
                assert (cycles[0].occurrence_version, cycles[0].state) == (1, "superseded")
                assert (cycles[1].occurrence_version, cycles[1].state) == (2, "open")
                assert cycles[1].expected_end_at == NOW + timedelta(hours=3)
                assert len(intents) == 1 and intents[0].state == "skipped"
                assert attempts == []
        finally:
            await engine.dispose()

    _run(scenario)


def test_occurrence_cancellation_event_suppresses_due_attempt(runtime_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, occurrence_id, _plan_id = await _seed(sessions)
        await _prioritize_owner_initial_job(sessions, owner_id=owner_id)
        async with sessions() as session, session.begin():
            await schedule_due_accountability(
                session,
                profiles=(_profile(owner_id),),
                today_untimed=_NoTodayItems(),
                now=NOW,
            )
            occurrence = await session.get(OccurrenceRecord, occurrence_id)
            assert occurrence is not None
            occurrence.state = "cancelled"
            occurrence.version += 1
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.occurrence_cancelled",
                    schema_version=1,
                    aggregate_id=occurrence_id,
                    aggregate_version=occurrence.version,
                    owner_id=owner_id,
                    occurred_at=NOW,
                    payload={"occurrence_id": str(occurrence_id)},
                ),
            )

        transport = _Transport(sessions)
        try:
            assert await process_delivery_iteration(
                sessions, transport, worker_id="accountability-runtime", now=lambda: NOW
            )
            assert transport.messages == []
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                intent = await session.scalar(
                    select(AccountabilityIntentRecord).where(
                        AccountabilityIntentRecord.owner_id == owner_id,
                        AccountabilityIntentRecord.slot == "initial",
                    )
                )
                attempts = list(
                    (
                        await session.scalars(
                            select(AccountabilityDeliveryAttemptRecord).where(
                                AccountabilityDeliveryAttemptRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert cycle is not None and cycle.state == "cancelled"
                assert intent is not None and intent.state == "skipped"
                assert attempts == []
        finally:
            await engine.dispose()

    _run(scenario)


def test_series_occurrence_created_and_rescheduled_events_open_current_cycles(
    runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(runtime_database)
        sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        owner_id, _original_occurrence_id, plan_id = await _seed(sessions)
        occurrence_id = uuid4()
        expected_end = NOW - timedelta(minutes=2)
        async with sessions() as session, session.begin():
            session.add(
                OccurrenceRecord(
                    id=occurrence_id,
                    owner_id=owner_id,
                    plan_item_id=plan_id,
                    kind="appointment",
                    event_zone="UTC",
                    local_date=NOW.date(),
                    local_time=(NOW - timedelta(hours=1)).time().replace(tzinfo=None),
                    fold=None,
                    end_local_date=NOW.date(),
                    end_local_time=(expected_end).time().replace(tzinfo=None),
                    end_fold=None,
                    starts_at=NOW - timedelta(hours=1),
                    ends_at=expected_end,
                    deadline_at=None,
                    duration_seconds=3600,
                    state="open",
                    version=1,
                    reminder_preferences_version=1,
                    created_at=NOW,
                )
            )
            await session.flush()
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_occurrence_created",
                    schema_version=1,
                    aggregate_id=occurrence_id,
                    aggregate_version=1,
                    owner_id=owner_id,
                    occurred_at=NOW,
                    payload={"occurrence_id": str(occurrence_id)},
                ),
            )
            reads = PlanningAccountabilityReads(session)
            assert (
                await consume_planning_cycle_events(session, current_occurrences=reads, at=NOW) == 1
            )

        async with sessions() as session, session.begin():
            occurrence = await session.get(OccurrenceRecord, occurrence_id)
            assert occurrence is not None
            occurrence.ends_at = NOW + timedelta(hours=2)
            occurrence.local_time = (NOW + timedelta(hours=1)).time().replace(tzinfo=None)
            occurrence.end_local_time = (NOW + timedelta(hours=2)).time().replace(tzinfo=None)
            occurrence.version = 2
            await add_outbox_event(
                session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_occurrence_rescheduled",
                    schema_version=1,
                    aggregate_id=occurrence_id,
                    aggregate_version=2,
                    owner_id=owner_id,
                    occurred_at=NOW,
                    payload={"occurrence_id": str(occurrence_id)},
                ),
            )
            reads = PlanningAccountabilityReads(session)
            assert (
                await consume_planning_cycle_events(session, current_occurrences=reads, at=NOW) == 1
            )

        try:
            async with sessions() as session:
                cycles = list(
                    (
                        await session.scalars(
                            select(CheckInCycleRecord)
                            .where(
                                CheckInCycleRecord.owner_id == owner_id,
                                CheckInCycleRecord.occurrence_id == occurrence_id,
                            )
                            .order_by(CheckInCycleRecord.occurrence_version)
                        )
                    ).all()
                )
                assert len(cycles) == 2
                assert cycles[0].state == "superseded"
                assert cycles[1].state == "open"
                assert cycles[1].expected_end_at == NOW + timedelta(hours=2)
        finally:
            await engine.dispose()

    _run(scenario)
