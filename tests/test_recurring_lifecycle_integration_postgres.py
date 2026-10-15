from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.mutations import MUTATION_CALLBACK_PREFIX
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.application.delivery import ReminderSend, TelegramReplySend
from pin.contexts.notifications.infrastructure.delivery_worker import enqueue_due_reminder_jobs
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue25_recurring_"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_RECURRING_LIFECYCLE_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_RECURRING_LIFECYCLE_DATABASE_URL to the assigned fresh fixture")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("recurring lifecycle proof requires the psycopg PostgreSQL driver")
    if (url.host, url.port, url.username) != ("127.0.0.1", DATABASE_PORT, DATABASE_USER):
        pytest.fail("recurring lifecycle proof refuses a different PostgreSQL endpoint/user")
    if not url.database or not url.database.startswith(DATABASE_PREFIX):
        pytest.fail("recurring lifecycle proof refuses a database outside its assigned prefix")
    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            database = str(connection.exec_driver_sql("select current_database()").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
            version = str(connection.exec_driver_sql("show server_version").scalar_one())
    finally:
        engine.dispose()
    if database != url.database or user != DATABASE_USER or port != DATABASE_PORT:
        pytest.fail("recurring lifecycle proof connected to a different database identity")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("recurring lifecycle proof refuses an unassigned PostgreSQL data directory")
    if version != "18.6":
        pytest.fail("recurring lifecycle proof requires the assigned PostgreSQL 18.6 cluster")
    return value


@pytest.fixture(scope="session")
def recurring_lifecycle_database() -> Iterator[str]:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    yield value


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class _InterpreterTransport:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls = 0

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("capture unexpectedly attempted a second model call")
        return {
            "id": f"recurring-local-{uuid4().hex}",
            "model": ROUTE,
            "choices": [{"message": {"content": json.dumps(self.result)}}],
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 40,
                "total_tokens": 120,
                "cost": "0.0000031",
            },
        }


class _LocalDeliveryTransport:
    """Run real delivery consumers while keeping every transport attempt local."""

    def __init__(self) -> None:
        self.reminders: list[ReminderSend] = []
        self.replies: list[TelegramReplySend] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        from pin.adapters.telegram.ingress import TelegramOwnerRecord

        destination = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )
        return int(destination) if destination is not None else None

    async def send_reminder(self, message: ReminderSend) -> str:
        self.reminders.append(message)
        return f"synthetic-recurring-reminder-{len(self.reminders):03d}"

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.replies.append(message)
        return f"synthetic-recurring-reply-{len(self.replies):03d}"


async def _seed_owner(
    sessions: async_sessionmaker[AsyncSession], *, bot_id: int, user_id: int, at: datetime
) -> UUID:
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language="en",
                home_zone="Asia/Almaty",
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
                period_id=f"recurring-local-{uuid4().hex}",
                policy_kind=PeriodKind.PAID.value,
                policy_version="local-integration-v1",
                starts_at=at - timedelta(days=1),
                ends_at=at + timedelta(days=180),
                ai_turn_limit=1_000,
                voice_second_limit=3_600,
            )
        )
        return owner_id


async def _submit(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    at: datetime,
    text_value: str | None = None,
    callback: str | None = None,
    assistant: BoundedPlanningAssistant | None = None,
) -> dict[str, object]:
    update_id = uuid4().int % 9_000_000_000_000_000
    if callback is None:
        assert text_value is not None
        payload: dict[str, object] = {
            "update_id": update_id,
            "message": {
                "from": {"id": user_id, "is_bot": False, "first_name": "Local fixture"},
                "chat": {"id": user_id, "type": "private"},
                "date": int(at.timestamp()),
                "text": text_value,
            },
        }
    else:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"recurring-callback-{uuid4().hex}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Local fixture"},
                "message": {"message_id": 1, "chat": {"id": user_id, "type": "private"}},
                "data": callback,
            },
        }
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id,
                InboxUpdate.update_id == update_id,
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        owner_id = inbox.owner_id
    claim = await claim_application_job(sessions, worker_id=f"recurring-local-{uuid4().hex[:12]}")
    assert claim is not None and claim.owner_id == owner_id
    await process_application_job(sessions, claim, now=at, assistant=assistant)
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        job = await session.get(Job, claim.job_id)
        assert event is not None and job is not None and job.status == "succeeded"
        return cast(dict[str, object], event.payload["payload"])


def _callback(payload: dict[str, object], *, prefix: str, label: str | None = None) -> str:
    markup = payload.get("reply_markup")
    assert isinstance(markup, dict)
    rows = markup.get("inline_keyboard")
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, list)
        for button in row:
            if not isinstance(button, dict):
                continue
            token, caption = button.get("callback_data"), button.get("text")
            if (
                isinstance(token, str)
                and token.startswith(prefix)
                and (
                    label is None
                    or (isinstance(caption, str) and label.casefold() in caption.casefold())
                )
            ):
                return token
    raise AssertionError(f"no callback found for {prefix!r} and label {label!r}")


async def _drain(
    sessions: async_sessionmaker[AsyncSession], transport: _LocalDeliveryTransport, *, at: datetime
) -> None:
    for _ in range(100):
        if not await process_delivery_iteration(
            sessions,
            transport,
            worker_id="issue25-recurring-local",
            now=lambda: at,
        ):
            return
    raise AssertionError("actual delivery worker did not become idle within the bounded drain")


async def _complete_occurrence(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    occurrence_id: UUID,
    at: datetime,
    transport: _LocalDeliveryTransport,
) -> None:
    preview = await _submit(
        sessions,
        bot_id=bot_id,
        user_id=user_id,
        at=at,
        text_value=f"/done {occurrence_id}",
    )
    assert preview.get("reply_markup") is not None
    confirmed = await _submit(
        sessions,
        bot_id=bot_id,
        user_id=user_id,
        at=at,
        callback=_callback(preview, prefix=MUTATION_CALLBACK_PREFIX, label="Confirm"),
    )
    assert "handled" == confirmed.get("handling")
    await _drain(sessions, transport, at=at)


def test_recurring_this_and_future_reconciles_delivery_end_to_end(
    recurring_lifecycle_database: str,
    record_property: Callable[[str, str], None],
) -> None:
    async def scenario() -> None:
        engine = create_engine(recurring_lifecycle_database)
        sessions = create_session_factory(engine)
        clock = _Clock(datetime(2026, 4, 27, 9, 0, tzinfo=UTC))
        bot_id = uuid4().int % 800_000_000 + 8_300_000_000
        user_id = uuid4().int % 800_000_000 + 8_400_000_000
        transport = _LocalDeliveryTransport()
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=user_id, at=clock.now())
            interpreter_transport = _InterpreterTransport(
                {
                    "kind": "proposal",
                    "language": "en",
                    "operation": "propose_create_plan",
                    "fields": {
                        "title": "Workout",
                        "schedule": {
                            "kind": "task",
                            "timing": "timed",
                            "local_date": "2026-05-04",
                            "local_time": "14:00:00",
                            "timezone": "Asia/Almaty",
                        },
                        "recurrence": {
                            "frequency": "weekly",
                            "interval": 2,
                            "weekdays": [0],
                            "occurrence_count": 5,
                        },
                    },
                }
            )
            assistant = BoundedPlanningAssistant(
                transport=interpreter_transport,
                usage=SqlAlchemyProviderUsageRecorder(sessions),
                allowance=SqlAlchemyTurnAllowance(sessions),
                now=clock.now,
            )
            preview = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                text_value="Plan a workout every two weeks starting May 4",
                assistant=assistant,
            )
            assert interpreter_transport.calls == 1
            capture_confirmation = _callback(preview, prefix="capc:")
            assert all(
                day in str(preview["plain_text"])
                for day in ("2026-05-04", "2026-05-18", "2026-06-01", "2026-06-15", "2026-06-29")
            )
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OccurrenceRecord)
                        .where(OccurrenceRecord.owner_id == owner_id)
                    )
                    == 0
                )
            confirmed = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                callback=capture_confirmation,
            )
            assert confirmed.get("handling") == "handled"
            assert interpreter_transport.calls == 1
            await _drain(sessions, transport, at=clock.now())

            assert (
                await process_pending_reminder_schedules(sessions, now=clock.now(), limit=1_000)
                >= 1
            )
            async with sessions() as session:
                series = await session.scalar(
                    select(SeriesRecord).where(SeriesRecord.owner_id == owner_id)
                )
                assert series is not None and series.total_count == 5
                initial_rows = list(
                    (
                        await session.execute(
                            select(SeriesOccurrenceRecord, OccurrenceRecord)
                            .join(
                                OccurrenceRecord,
                                OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id,
                            )
                            .where(SeriesOccurrenceRecord.series_id == series.id)
                            .order_by(SeriesOccurrenceRecord.sequence)
                        )
                    ).all()
                )
                assert len(initial_rows) == 5
                occurrence_ids = {
                    association.sequence: occurrence.id for association, occurrence in initial_rows
                }
                assert [occurrence.local_date.isoformat() for _, occurrence in initial_rows] == [
                    "2026-05-04",
                    "2026-05-18",
                    "2026-06-01",
                    "2026-06-15",
                    "2026-06-29",
                ]
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ReminderIntentRecord)
                        .where(ReminderIntentRecord.series_id == series.id)
                    )
                    == 10
                )

            for sequence, day, reminder_at, due_at in (
                (
                    0,
                    "2026-05-04",
                    datetime(2026, 5, 3, 9, tzinfo=UTC),
                    datetime(2026, 5, 4, 8, tzinfo=UTC),
                ),
                (
                    1,
                    "2026-05-18",
                    datetime(2026, 5, 17, 9, tzinfo=UTC),
                    datetime(2026, 5, 18, 8, tzinfo=UTC),
                ),
            ):
                clock.at = reminder_at
                await _drain(sessions, transport, at=clock.now())
                clock.at = due_at
                await _drain(sessions, transport, at=clock.now())
                clock.at = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(hours=9)
                await _complete_occurrence(
                    sessions,
                    bot_id=bot_id,
                    user_id=user_id,
                    occurrence_id=occurrence_ids[sequence],
                    at=clock.now(),
                    transport=transport,
                )
                assert (
                    await process_pending_reminder_schedules(sessions, now=clock.now(), limit=1_000)
                    >= 1
                )

            async with sessions() as session, session.begin():
                old_intent = await session.scalar(
                    select(ReminderIntentRecord).where(
                        ReminderIntentRecord.occurrence_id == occurrence_ids[2],
                        ReminderIntentRecord.offset_kind == "calendar_days",
                    )
                )
                assert old_intent is not None and old_intent.state == "scheduled"
            clock.at = datetime(2026, 5, 31, 9, 0, tzinfo=UTC)
            async with sessions() as session, session.begin():
                assert await enqueue_due_reminder_jobs(session, now=clock.now(), limit=100) == 1
                stale_job = await session.scalar(
                    select(Job).where(
                        Job.lane == JobLane.DELIVERY.value,
                        Job.kind == "notifications.deliver_reminder",
                        Job.status == "pending",
                        Job.payload["intent_id"].astext == str(old_intent.id),
                    )
                )
                assert stale_job is not None

            menu = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                text_value=f"/reschedule {occurrence_ids[2]}",
            )
            scope_choice = _callback(
                menu,
                prefix=MUTATION_CALLBACK_PREFIX,
                label="This and future",
            )
            schedule_prompt = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                callback=scope_choice,
            )
            assert "date" in str(schedule_prompt["plain_text"]).casefold()
            changed_preview = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                text_value="2026-06-08 14:00",
            )
            for day in ("2026-06-08", "2026-06-22", "2026-07-06"):
                assert day in str(changed_preview["plain_text"])
            preview_text = str(changed_preview["plain_text"])
            assert "2026-06-01 14:00" in preview_text
            assert "(Asia/Almaty) \u2192 2026-06-08 14:00 (Asia/Almaty)" in preview_text
            mutation_confirm = _callback(
                changed_preview,
                prefix=MUTATION_CALLBACK_PREFIX,
                label="Reschedule",
            )
            mutation_result = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                callback=mutation_confirm,
            )
            assert mutation_result.get("handling") == "handled"
            assert interpreter_transport.calls == 1
            reminder_count_before_replay = len(transport.reminders)
            replayed_mutation = await _submit(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=clock.now(),
                callback=mutation_confirm,
            )
            assert "That preview is stale. Start again." in str(
                replayed_mutation.get("plain_text")
            )
            assert len(transport.reminders) == reminder_count_before_replay
            assert (
                await process_pending_reminder_schedules(
                    sessions, now=clock.now(), limit=1_000
                )
                >= 1
            )

            async with sessions() as session:
                rows = list(
                    (
                        await session.execute(
                            select(SeriesOccurrenceRecord, OccurrenceRecord)
                            .join(
                                OccurrenceRecord,
                                OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id,
                            )
                            .where(SeriesOccurrenceRecord.series_id == series.id)
                            .order_by(SeriesOccurrenceRecord.sequence)
                        )
                    ).all()
                )
                updated_series = await session.get(SeriesRecord, series.id)
                assert updated_series is not None
                assert updated_series.version == series.version + 1
                assert updated_series.total_count == 5
                assert updated_series.anchor_sequence == 2
                assert updated_series.anchor_date.isoformat() == "2026-06-08"
                assert len(rows) == 5
                assert {
                    association.sequence: occurrence.id for association, occurrence in rows
                } == occurrence_ids
                assert [occurrence.local_date.isoformat() for _, occurrence in rows] == [
                    "2026-05-04",
                    "2026-05-18",
                    "2026-06-08",
                    "2026-06-22",
                    "2026-07-06",
                ]
                assert [(occurrence.state, occurrence.version) for _, occurrence in rows[:2]] == [
                    ("completed", 2),
                    ("completed", 2),
                ]
                reconciled_intent = await session.get(ReminderIntentRecord, old_intent.id)
                assert reconciled_intent is not None
                assert reconciled_intent.state == "scheduled"
                assert reconciled_intent.dispatch_version == old_intent.dispatch_version + 1
                assert reconciled_intent.due_at == datetime(2026, 6, 7, 9, tzinfo=UTC)

            reminder_count = len(transport.reminders)
            await _drain(sessions, transport, at=clock.now())
            assert len(transport.reminders) == reminder_count
            async with sessions() as session:
                stale_attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == old_intent.id,
                        DeliveryAttemptRecord.outcome == "skipped",
                        DeliveryAttemptRecord.failure_code == "stale_intent_version",
                    )
                )
                assert stale_attempt is not None
                assert stale_attempt.dispatch_version == old_intent.dispatch_version
                stale_outcome_event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                        OutboxEvent.aggregate_id == stale_attempt.id,
                    )
                )
                assert stale_outcome_event is not None
                stale_outcome_payload = stale_outcome_event.payload.get("payload")
                assert isinstance(stale_outcome_payload, dict)
                assert stale_outcome_payload.get("dispatch_version") == old_intent.dispatch_version

            clock.at = datetime(2026, 6, 7, 9, 0, tzinfo=UTC)
            before_revised = len(transport.reminders)
            await _drain(sessions, transport, at=clock.now())
            assert len(transport.reminders) == before_revised + 1
            async with sessions() as session:
                revised_attempts = list(
                    await session.scalars(
                        select(DeliveryAttemptRecord)
                        .join(
                            ReminderIntentRecord,
                            ReminderIntentRecord.id == DeliveryAttemptRecord.intent_id,
                        )
                        .where(
                            ReminderIntentRecord.occurrence_id == occurrence_ids[2],
                            ReminderIntentRecord.due_at == clock.now(),
                            DeliveryAttemptRecord.outcome == "confirmed",
                        )
                    )
                )
                assert len(revised_attempts) == 1
                assert (
                    revised_attempts[0].dispatch_version
                    == old_intent.dispatch_version + 1
                )
                revised_outcome_event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                        OutboxEvent.aggregate_id == revised_attempts[0].id,
                    )
                )
                assert revised_outcome_event is not None
                revised_outcome_payload = revised_outcome_event.payload.get("payload")
                assert isinstance(revised_outcome_payload, dict)
                assert (
                    revised_outcome_payload.get("dispatch_version")
                    == revised_attempts[0].dispatch_version
                )
            sent_count = len(transport.reminders)
            await _drain(sessions, transport, at=clock.now())
            assert len(transport.reminders) == sent_count
            record_property("reference_instant", "2026-04-27T09:00:00Z")
            record_property("mutation_instant", "2026-05-31T09:00:00Z")
            record_property("revised_reminder_dispatch", "2026-06-07T09:00:00Z")
        finally:
            await engine.dispose()

    run_local_async(scenario)
