from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import ReminderSend, TelegramReplySend
from pin.contexts.notifications.infrastructure.delivery_worker import (
    deliver_reminder_job,
    enqueue_due_reminder_jobs,
)
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
    resolve_schedule,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobClaim, JobLane, claim_job, enqueue_job
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_delivery_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime(2026, 10, 14, 9, tzinfo=UTC)
RESCHEDULED_START = datetime(2026, 10, 16, 13, tzinfo=UTC)
RESCHEDULED_DATE = RESCHEDULED_START.date()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_MUTATION_DELIVERY_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_MUTATION_DELIVERY_DATABASE_URL to the assigned Issue 15 delivery fixture"
        )
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX):
        pytest.fail("mutation delivery database is outside its assigned fixture prefix")
    if port != DATABASE_PORT or user != "pin_test":
        pytest.fail("mutation delivery PostgreSQL does not match its assigned user and port")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("mutation delivery database is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def mutation_delivery_database_url() -> str:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


class _ReminderTransport:
    def __init__(
        self, *, entered: asyncio.Event | None = None, release: asyncio.Event | None = None
    ):
        self.entered = entered
        self.release = release
        self.sent: list[ReminderSend] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        return cast(
            int | None,
            await session.scalar(
                select(TelegramOwnerRecord.telegram_user_id).where(
                    TelegramOwnerRecord.owner_id == owner_id
                )
            ),
        )

    async def send_reminder(self, message: ReminderSend) -> str:
        self.sent.append(message)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return f"mutation-delivery-{len(self.sent)}"

    async def send_reply(self, message: TelegramReplySend) -> str:
        raise AssertionError("reminder transport cannot send Telegram replies")


@dataclass(frozen=True, slots=True)
class _Fixture:
    owner_id: UUID
    user_id: int
    bot_id: int
    plan_id: UUID
    occurrence_id: UUID
    intent_id: UUID
    due_at: datetime


async def _seed_delivery_case(
    sessions: async_sessionmaker[AsyncSession], *, at: datetime
) -> _Fixture:
    user_id = uuid4().int % 2_000_000_000 + 1
    bot_id = uuid4().int % 2_000_000_000 + 1
    plan_id, occurrence_id, intent_id = uuid4(), uuid4(), uuid4()
    start_at = at.astimezone(UTC) + timedelta(minutes=20)
    due_at = start_at - timedelta(hours=1)
    draft = OneOffPlanDraft(
        kind=PlanKind.TASK,
        title="Reminder delivery mutation race",
        local_date=start_at.date(),
        local_time=start_at.time().replace(tzinfo=None),
        event_zone="UTC",
        reminders=(ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "default"),),
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
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
                version=1,
            )
        )
        session.add(
            OnboardingRecord(
                owner_id=owner_id,
                draft_id=uuid4(),
                mode="initial",
                step="complete",
                draft={},
                base_profile_version=1,
                version=1,
            )
        )
        session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=None))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"issue15-delivery-{owner_id}",
                policy_kind="paid",
                policy_version="test-v1",
                starts_at=at.astimezone(UTC) - timedelta(days=1),
                ends_at=at.astimezone(UTC) + timedelta(days=7),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            PlanItemRecord(
                id=plan_id,
                owner_id=owner_id,
                kind=PlanKind.TASK.value,
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
                state="open",
                version=1,
                reminder_preferences_version=1,
            )
        )
        await session.flush()
        source = EventEnvelope(
            id=uuid4(),
            event_type="planning.plan_created",
            schema_version=1,
            aggregate_id=occurrence_id,
            aggregate_version=1,
            owner_id=owner_id,
            occurred_at=at.astimezone(UTC),
            payload={"test_fixture": "mutation_delivery"},
        )
        await add_outbox_event(session, source)
        session.add(
            ReminderRuleRecord(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind=OffsetKind.ELAPSED_SECONDS.value,
                offset_amount=3600,
                source="default",
                due_at=due_at,
            )
        )
        session.add(
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
                offset_kind=OffsetKind.ELAPSED_SECONDS.value,
                offset_amount=3600,
                source="default",
                due_at=due_at,
                not_before_at=None,
                state="scheduled",
                dispatch_version=1,
                source_event_id=source.id,
            )
        )
        await enqueue_job(
            session,
            lane=JobLane.DELIVERY,
            kind="notifications.deliver_reminder",
            payload={"intent_id": str(intent_id), "dispatch_version": 1},
            deduplication_key=f"reminder:{intent_id}:1",
            owner_id=owner_id,
            priority=1_000_000,
        )
    return _Fixture(owner_id, user_id, bot_id, plan_id, occurrence_id, intent_id, due_at)


async def _dispatch_update(
    sessions: async_sessionmaker[AsyncSession],
    *,
    fixture: _Fixture,
    at: datetime,
    text: str | None = None,
    callback: str | None = None,
) -> dict[str, object]:
    update_id = uuid4().int % 9_000_000_000_000_000
    if callback is None:
        payload: dict[str, object] = {
            "update_id": update_id,
            "message": {
                "from": {"id": fixture.user_id, "is_bot": False, "first_name": "Mutation test"},
                "chat": {"id": fixture.user_id, "type": "private"},
                "date": int(at.timestamp()),
                "text": text,
            },
        }
    else:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"mutation-delivery-{uuid4().hex}",
                "from": {"id": fixture.user_id, "is_bot": False, "first_name": "Mutation test"},
                "message": {
                    "message_id": 1,
                    "chat": {"id": fixture.user_id, "type": "private"},
                },
                "data": callback,
            },
        }
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=fixture.bot_id,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == fixture.bot_id,
                InboxUpdate.update_id == update_id,
            )
        )
        assert inbox is not None and inbox.owner_id == fixture.owner_id
        job = await session.scalar(
            select(Job)
            .where(
                Job.owner_id == fixture.owner_id,
                Job.lane == JobLane.APPLICATION.value,
                Job.status == "pending",
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        assert job is not None
        job.priority = 2_147_483_647
    claim = await claim_application_job(sessions, worker_id=f"issue15-mutation-{uuid4().hex[:10]}")
    assert claim is not None
    await process_application_job(sessions, claim, now=at)
    async with sessions() as session:
        reply = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert reply is not None
        job = await session.get(Job, claim.job_id)
        assert job is not None and job.status == "succeeded"
        return cast(dict[str, object], reply.payload["payload"])


def _button(reply: dict[str, object], *, label_contains: str | None = None) -> str:
    markup = cast(dict[str, object], reply["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    for row in rows:
        for button in row:
            label = str(button.get("text", ""))
            token = button.get("callback_data")
            if isinstance(token, str) and (label_contains is None or label_contains in label):
                return token
    raise AssertionError(f"no callback matched button text {label_contains!r}")


async def _confirm_mutation(
    sessions: async_sessionmaker[AsyncSession], *, fixture: _Fixture, action: str, at: datetime
) -> dict[str, object]:
    menu = await _dispatch_update(sessions, fixture=fixture, at=at, text=f"/{action}")
    target_preview = await _dispatch_update(
        sessions, fixture=fixture, at=at, callback=_button(menu)
    )
    if action == "reschedule":
        assert target_preview.get("reply_markup") is None
        preview = await _dispatch_update(
            sessions,
            fixture=fixture,
            at=at,
            text=RESCHEDULED_START.strftime("%Y-%m-%d %H:%M"),
        )
    else:
        preview = target_preview
    return await _dispatch_update(sessions, fixture=fixture, at=at, callback=_button(preview))


async def _claim_reminder(
    sessions: async_sessionmaker[AsyncSession],
    intent_id: UUID,
    *,
    dispatch_version: int = 1,
) -> JobClaim:
    for _ in range(100):
        async with sessions() as session, session.begin():
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id=f"issue15-delivery-{uuid4().hex[:10]}",
                lease_for=timedelta(minutes=2),
            )
        if claim is None:
            break
        if (
            claim.payload.get("intent_id") == str(intent_id)
            and claim.payload.get("dispatch_version") == dispatch_version
        ):
            return claim
    raise AssertionError("could not claim the fixture's scheduled reminder job")


@pytest.mark.parametrize("action", ("cancel", "done", "reschedule"))
def test_claimed_old_reminder_is_suppressed_after_native_confirmed_mutation(
    mutation_delivery_database_url: str, action: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(mutation_delivery_database_url)
        sessions = create_session_factory(engine)
        try:
            fixture = await _seed_delivery_case(sessions, at=NOW)
            claim = await _claim_reminder(sessions, fixture.intent_id)
            assert claim.payload.get("intent_id") == str(fixture.intent_id)

            preview_result = await _confirm_mutation(
                sessions, fixture=fixture, action=action, at=NOW
            )
            assert preview_result["handling"] == "handled"
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, fixture.occurrence_id)
                assert occurrence is not None
                if action == "cancel":
                    assert (occurrence.state, occurrence.version) == ("cancelled", 2)
                elif action == "done":
                    assert (occurrence.state, occurrence.version) == ("completed", 2)
                else:
                    assert (occurrence.state, occurrence.version) == ("open", 2)
                    assert (occurrence.local_date, occurrence.local_time) == (
                        RESCHEDULED_DATE,
                        RESCHEDULED_START.time(),
                    )

            transport = _ReminderTransport()
            outcome = await deliver_reminder_job(
                sessions,
                claim,
                transport,
                identity_store_factory=SqlAlchemyIdentityStore,
                now=lambda: NOW,
            )
            assert outcome is not None and outcome.outcome.value == "skipped"
            assert outcome.failure_code == "obsolete_or_incomplete_schedule"
            assert transport.sent == []

            async with sessions() as session:
                attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == fixture.intent_id
                    )
                )
                intent = await session.get(ReminderIntentRecord, fixture.intent_id)
                job = await session.get(Job, claim.job_id)
                assert attempt is not None
                assert (attempt.outcome, attempt.failure_code) == (
                    "skipped",
                    "obsolete_or_incomplete_schedule",
                )
                assert intent is not None and intent.state == "scheduled"
                assert job is not None and job.status == "succeeded"
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                        OutboxEvent.aggregate_id == attempt.id,
                    )
                )
                assert event is not None
                assert event.payload["payload"]["outcome"] == "skipped"

            await process_pending_reminder_schedules(sessions, now=NOW + timedelta(seconds=1))
            async with sessions() as session:
                old_intent = await session.get(ReminderIntentRecord, fixture.intent_id)
                assert old_intent is not None
                if action in {"cancel", "done"}:
                    assert old_intent.state == "invalidated"
                else:
                    assert (old_intent.state, old_intent.occurrence_version) == (
                        "scheduled",
                        2,
                    )
                    assert old_intent.due_at == RESCHEDULED_START - timedelta(hours=1)
                intents = list(
                    (
                        await session.scalars(
                            select(ReminderIntentRecord).where(
                                ReminderIntentRecord.owner_id == fixture.owner_id,
                                ReminderIntentRecord.occurrence_id == fixture.occurrence_id,
                            )
                        )
                    ).all()
                )
                if action in {"cancel", "done"}:
                    assert all(item.state != "scheduled" for item in intents)
                else:
                    assert any(item.state == "scheduled" for item in intents)
                attempts = list(
                    (
                        await session.scalars(
                            select(DeliveryAttemptRecord).where(
                                DeliveryAttemptRecord.owner_id == fixture.owner_id
                            )
                        )
                    ).all()
                )
                assert len(attempts) == 1 and attempts[0].outcome == "skipped"

            if action == "reschedule":
                next_delivery_at = RESCHEDULED_START - timedelta(hours=1)
                async with sessions() as session, session.begin():
                    assert await enqueue_due_reminder_jobs(session, now=next_delivery_at) >= 1
                next_claim = await _claim_reminder(sessions, fixture.intent_id, dispatch_version=2)
                next_outcome = await deliver_reminder_job(
                    sessions,
                    next_claim,
                    transport,
                    identity_store_factory=SqlAlchemyIdentityStore,
                    now=lambda: next_delivery_at,
                )
                assert next_outcome is not None and next_outcome.outcome.value == "confirmed"
                assert len(transport.sent) == 1
                async with sessions() as session:
                    attempts = list(
                        (
                            await session.scalars(
                                select(DeliveryAttemptRecord).where(
                                    DeliveryAttemptRecord.owner_id == fixture.owner_id
                                )
                            )
                        ).all()
                    )
                    assert len(attempts) == 2
                    assert {attempt.outcome for attempt in attempts} == {"skipped", "confirmed"}
                    assert any(
                        attempt.dispatch_version == 2 and attempt.outcome == "confirmed"
                        for attempt in attempts
                    )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_cancel_after_started_receipt_cannot_retract_send_but_suppresses_followups(
    mutation_delivery_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(mutation_delivery_database_url)
        sessions = create_session_factory(engine)
        entered, release = asyncio.Event(), asyncio.Event()
        try:
            fixture = await _seed_delivery_case(sessions, at=NOW)
            claim = await _claim_reminder(sessions, fixture.intent_id)
            transport = _ReminderTransport(entered=entered, release=release)
            delivery = asyncio.create_task(
                deliver_reminder_job(
                    sessions,
                    claim,
                    transport,
                    identity_store_factory=SqlAlchemyIdentityStore,
                    now=lambda: NOW,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=5)

            async with sessions() as session:
                attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == fixture.intent_id
                    )
                )
                assert attempt is not None and attempt.outcome == "started"
                assert attempt.finished_at is None

            result = await _confirm_mutation(sessions, fixture=fixture, action="cancel", at=NOW)
            assert result["handling"] == "handled"
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, fixture.occurrence_id)
                assert occurrence is not None and (occurrence.state, occurrence.version) == (
                    "cancelled",
                    2,
                )
            # The provider call has crossed the durable started-receipt boundary. A later
            # cancellation cannot promise to retract it; only future intents are suppressed.
            release.set()
            outcome = await delivery
            assert outcome is not None and outcome.outcome.value == "confirmed"
            assert len(transport.sent) == 1

            await process_pending_reminder_schedules(sessions, now=NOW + timedelta(seconds=1))
            async with sessions() as session:
                attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == fixture.intent_id
                    )
                )
                intent = await session.get(ReminderIntentRecord, fixture.intent_id)
                job = await session.get(Job, claim.job_id)
                occurrence = await session.get(OccurrenceRecord, fixture.occurrence_id)
                assert attempt is not None
                assert attempt.outcome == "confirmed"
                assert attempt.provider_message_id == "mutation-delivery-1"
                assert attempt.finished_at is not None
                assert intent is not None and intent.state == "invalidated"
                assert occurrence is not None and occurrence.state == "cancelled"
                assert job is not None and job.status == "succeeded"
                scheduled = await session.scalar(
                    select(ReminderIntentRecord.id).where(
                        ReminderIntentRecord.owner_id == fixture.owner_id,
                        ReminderIntentRecord.occurrence_id == fixture.occurrence_id,
                        ReminderIntentRecord.state == "scheduled",
                    )
                )
                assert scheduled is None
                attempts = list(
                    (
                        await session.scalars(
                            select(DeliveryAttemptRecord).where(
                                DeliveryAttemptRecord.owner_id == fixture.owner_id
                            )
                        )
                    ).all()
                )
                assert len(attempts) == 1

            assert len(transport.sent) == 1
            await process_delivery_iteration(
                sessions,
                transport,
                worker_id=f"issue15-no-follow-up-{uuid4().hex[:8]}",
                now=lambda: NOW + timedelta(seconds=2),
            )
            assert len(transport.sent) == 1
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(DeliveryAttemptRecord.id).where(
                            DeliveryAttemptRecord.owner_id == fixture.owner_id,
                            DeliveryAttemptRecord.intent_id == fixture.intent_id,
                            DeliveryAttemptRecord.outcome == "started",
                        )
                    )
                    is None
                )
                assert (
                    await session.scalar(
                        select(ReminderIntentRecord.id).where(
                            ReminderIntentRecord.owner_id == fixture.owner_id,
                            ReminderIntentRecord.occurrence_id == fixture.occurrence_id,
                            ReminderIntentRecord.state == "scheduled",
                        )
                    )
                    is None
                )
        finally:
            if not release.is_set():
                release.set()
            await engine.dispose()

    run_local_async(scenario)
