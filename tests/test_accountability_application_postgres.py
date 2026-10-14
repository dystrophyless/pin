from __future__ import annotations

import os
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.mutation_runtime_store import MutationRuntimePendingRecord
from pin.adapters.telegram.mutations import MUTATION_CALLBACK_PREFIX
from pin.contexts.accountability.application.contracts import CurrentOccurrence
from pin.contexts.accountability.infrastructure.models import CheckInCycleRecord
from pin.contexts.accountability.infrastructure.scheduler import synchronize_occurrence
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.application.delivery import ReminderSend, TelegramReplySend
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue16_app_"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


def _database_url() -> str:
    value = os.getenv("PIN_TEST_ACCOUNTABILITY_APPLICATION_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_ACCOUNTABILITY_APPLICATION_DATABASE_URL to the assigned "
            "Issue 16 application database"
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
        pytest.fail("Accountability application proof requires its own pin_issue16_app_ database")
    if user != DATABASE_USER or port != DATABASE_PORT:
        pytest.fail(
            "Accountability application proof connected to the wrong PostgreSQL user or port"
        )
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail(
            "Accountability application proof connected outside the assigned disposable cluster"
        )
    if version != "18.6":
        pytest.fail(
            "Accountability application proof requires the assigned PostgreSQL 18.6 server"
        )
    return value


@pytest.fixture(scope="session")
def application_database() -> Iterator[str]:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    yield value


class _Transport:
    def __init__(self) -> None:
        self.messages: list[TelegramReplySend] = []

    async def resolve_destination(self, _session: AsyncSession, _owner_id: UUID) -> int:
        return 987654321

    async def send_reminder(self, _message: ReminderSend) -> str | None:
        raise AssertionError("accountability application proof sent an ordinary reminder")

    async def send_reply(self, message: TelegramReplySend) -> str | None:
        self.messages.append(message)
        return f"fake-message-{len(self.messages)}"


async def _seed_owner_and_occurrence(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    telegram_user_id: int,
    at: datetime,
) -> tuple[UUID, UUID]:
    occurrence_id, plan_id = uuid4(), uuid4()
    start_at, end_at = at - timedelta(hours=2), at - timedelta(minutes=1)
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=telegram_user_id
        )
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"issue16-app-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="issue16-app-v1",
                starts_at=at - timedelta(days=1),
                ends_at=at + timedelta(days=30),
                ai_turn_limit=100,
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
                quiet_start=time((at.hour + 3) % 24, at.minute),
                quiet_end=time((at.hour + 4) % 24, at.minute),
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
        session.add(
            PlanItemRecord(
                id=plan_id,
                owner_id=owner_id,
                kind="appointment",
                title="Prepare the report",
                version=1,
                created_at=at,
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
                local_date=at.date(),
                local_time=start_at.time().replace(tzinfo=None),
                fold=None,
                end_local_date=at.date(),
                end_local_time=end_at.time().replace(tzinfo=None),
                end_fold=None,
                starts_at=start_at,
                ends_at=end_at,
                deadline_at=None,
                duration_seconds=3600,
                state="open",
                version=1,
                reminder_preferences_version=1,
                created_at=at,
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
                title="Prepare the report",
                event_zone="UTC",
                local_date=at.date(),
                starts_at=start_at,
                ends_at=end_at,
                deadline_at=None,
            ),
            now=at,
        )
    return owner_id, occurrence_id


async def _persist_private_callback(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    telegram_user_id: int,
    callback_data: str,
) -> UUID:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "callback_query": {
            "id": f"issue16-app-{uuid4().hex}",
            "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Test"},
            "message": {
                "message_id": 1,
                "chat": {"id": telegram_user_id, "type": "private"},
            },
            "data": callback_data,
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
        job = await session.scalar(
            select(Job)
            .where(
                Job.owner_id == inbox.owner_id,
                Job.lane == JobLane.APPLICATION.value,
                Job.status == "pending",
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        assert job is not None
        job.priority = 2_147_483_647
        return inbox.id


async def _process_private_callback(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    telegram_user_id: int,
    callback_data: str,
    at: datetime,
) -> tuple[UUID, dict[str, object]]:
    inbox_id = await _persist_private_callback(
        sessions,
        bot_id=bot_id,
        telegram_user_id=telegram_user_id,
        callback_data=callback_data,
    )
    claim = await claim_application_job(sessions, worker_id=f"issue16-app-{uuid4().hex[:12]}")
    assert claim is not None
    assert claim.payload.get("inbox_id") == str(inbox_id)
    await process_application_job(sessions, claim, now=at, assistant=None)
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        return claim.job_id, cast(dict[str, object], event.payload["payload"])


def _callback_with_prefix(payload: dict[str, object], prefix: str) -> str:
    markup = cast(dict[str, object], payload["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    callbacks = [
        button["callback_data"]
        for row in rows
        for button in row
        if isinstance(button.get("callback_data"), str)
        and str(button["callback_data"]).startswith(prefix)
    ]
    assert callbacks
    return cast(str, callbacks[0])


def _run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    run_local_async(main)


def test_private_accountability_callbacks_preview_confirm_and_close_cycle(
    application_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(application_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
        telegram_user_id = 987654321
        bot_id = 2_000_000_000 + (uuid4().int % 1_000_000_000)
        transport = _Transport()
        try:
            owner_id, occurrence_id = await _seed_owner_and_occurrence(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                at=at,
            )

            assert await process_delivery_iteration(
                sessions,
                transport,
                worker_id=f"issue16-delivery-{uuid4().hex[:10]}",
                now=lambda: at,
            )
            assert len(transport.messages) == 1
            delivered_markup = transport.messages[0].reply_markup
            assert delivered_markup is not None
            delivered_rows = cast(
                list[list[dict[str, object]]], delivered_markup["inline_keyboard"]
            )
            action_callback = cast(str, delivered_rows[0][0]["callback_data"])
            assert action_callback.startswith("ac:")

            # A different private actor cannot consume the delivery's owner-bound action token.
            _, wrong_actor_reply = await _process_private_callback(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id + 1,
                callback_data=action_callback,
                at=at + timedelta(minutes=1),
            )
            assert isinstance(wrong_actor_reply.get("markdown"), str)
            assert cast(str, wrong_actor_reply["markdown"]).strip()
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "open"

            before_calls: int
            before_reservations: int
            async with sessions() as session:
                before_calls = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ProviderCallRecord)
                        .where(ProviderCallRecord.owner_id == owner_id)
                    )
                    or 0
                )
                before_reservations = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageReservationRecord)
                        .where(UsageReservationRecord.owner_id == owner_id)
                    )
                    or 0
                )
            assert before_calls == 0 and before_reservations == 0

            _, preview_reply = await _process_private_callback(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                callback_data=action_callback,
                at=at + timedelta(minutes=2),
            )
            assert "mark this occurrence done" in cast(str, preview_reply["markdown"]).casefold()
            confirm_callback = _callback_with_prefix(preview_reply, MUTATION_CALLBACK_PREFIX)
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "open"
                pending = await session.scalar(
                    select(MutationRuntimePendingRecord).where(
                        MutationRuntimePendingRecord.owner_id == owner_id
                    )
                )
                assert pending is not None
                calls = await session.scalar(
                    select(func.count())
                    .select_from(ProviderCallRecord)
                    .where(ProviderCallRecord.owner_id == owner_id)
                )
                reservations = await session.scalar(
                    select(func.count())
                    .select_from(UsageReservationRecord)
                    .where(UsageReservationRecord.owner_id == owner_id)
                )
                assert calls == before_calls and reservations == before_reservations

            # The initial accountability action is one-use; replay emits a safe reply only.
            _, replay_reply = await _process_private_callback(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                callback_data=action_callback,
                at=at + timedelta(minutes=3),
            )
            assert "invalid" in cast(str, replay_reply["markdown"]).casefold()
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "open"

            _, confirmation_reply = await _process_private_callback(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                callback_data=confirm_callback,
                at=at + timedelta(minutes=4),
            )
            assert "done" in cast(str, confirmation_reply["markdown"]).casefold()
            _, confirmation_replay_reply = await _process_private_callback(
                sessions,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                callback_data=confirm_callback,
                at=at + timedelta(minutes=5),
            )
            assert "preview is stale" in cast(
                str, confirmation_replay_reply["markdown"]
            ).casefold()

            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "completed"
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.state == "open"
                completed_events = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.owner_id == owner_id,
                                OutboxEvent.event_type == "planning.occurrence_completed",
                                OutboxEvent.aggregate_id == occurrence_id,
                            )
                        )
                    ).all()
                )
                assert len(completed_events) == 1

            await process_delivery_iteration(
                sessions,
                transport,
                worker_id=f"issue16-delivery-{uuid4().hex[:10]}",
                now=lambda: at + timedelta(minutes=6),
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.state == "completed"
        finally:
            await engine.dispose()

    _run(scenario)
