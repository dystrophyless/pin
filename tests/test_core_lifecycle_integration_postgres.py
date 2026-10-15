from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.accountability_composition import schedule_accountability_iteration
from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.mutations import MUTATION_CALLBACK_PREFIX
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityIntentRecord,
    CheckInCycleRecord,
)
from pin.contexts.conversation.application.service import (
    ROUTE,
    BoundedPlanningAssistant,
)
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.application.delivery import ReminderSend, TelegramReplySend
from pin.contexts.notifications.infrastructure.models import (
    AccountabilityDeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobClaim, JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue25_core_"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
BOT_BASE = 7_100_000_000
USER_BASE = 7_200_000_000


def _database_url() -> str:
    value = os.getenv("PIN_TEST_CORE_LIFECYCLE_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_CORE_LIFECYCLE_DATABASE_URL to the assigned fresh fixture")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("core lifecycle proof requires the psycopg PostgreSQL driver")
    if (url.host, url.port, url.username) != ("127.0.0.1", DATABASE_PORT, DATABASE_USER):
        pytest.fail("core lifecycle proof refuses a different PostgreSQL endpoint/user")
    if not url.database or not url.database.startswith(DATABASE_PREFIX):
        pytest.fail("core lifecycle proof refuses a database outside pin_issue25_core_")
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
        pytest.fail("core lifecycle proof connected to a different database identity")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("core lifecycle proof refuses an unassigned PostgreSQL data directory")
    if version != "18.6":
        pytest.fail("core lifecycle proof requires the assigned PostgreSQL 18.6 cluster")
    return value


@pytest.fixture(scope="session")
def core_lifecycle_database() -> Iterator[str]:
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


class _DeterministicInterpreterTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if self.calls > 1:
            raise AssertionError(
                "capture clarification/callback unexpectedly called the model again"
            )
        return self.response


class _FakeTelegramTransport:
    """Exercise production consumers while recording only local fake attempts."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.reminders: list[tuple[str, str]] = []
        self.replies: list[tuple[str, object]] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        from pin.adapters.telegram.ingress import TelegramOwnerRecord

        destination = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )
        return int(destination) if destination is not None else None

    async def send_reminder(self, message: ReminderSend) -> str:
        attempt_id = f"fake-reminder-{len(self.reminders) + 1:03d}"
        self.reminders.append((attempt_id, message.text))
        return attempt_id

    async def send_reply(self, message: TelegramReplySend) -> str:
        attempt_id = f"fake-reply-{len(self.replies) + 1:03d}"
        self.replies.append((attempt_id, message))
        return attempt_id


def _provider_response(result: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"core-local-{uuid4().hex}",
        "model": ROUTE,
        "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
        "usage": {
            "prompt_tokens": 96,
            "completion_tokens": 48,
            "total_tokens": 144,
            "cost": "0.0000042",
        },
    }


async def _submit_update(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    payload: dict[str, object],
    at: datetime,
    assistant: BoundedPlanningAssistant | None = None,
) -> tuple[UUID, JobClaim, dict[str, object]]:
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        update_id = payload["update_id"]
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id,
                InboxUpdate.update_id == update_id,
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        owner_id = inbox.owner_id

    claim = await claim_application_job(
        sessions, worker_id=f"issue25-core-{uuid4().hex[:12]}"
    )
    assert claim is not None and claim.owner_id == owner_id
    await process_application_job(sessions, claim, now=at, assistant=assistant)
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        return owner_id, claim, cast(dict[str, object], event.payload["payload"])


async def _send_text(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    text_value: str,
    at: datetime,
    assistant: BoundedPlanningAssistant | None = None,
) -> tuple[UUID, JobClaim, dict[str, object]]:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id, "is_bot": False, "first_name": "Fixture"},
            "chat": {"id": user_id, "type": "private"},
            "date": int(at.timestamp()),
            "text": text_value,
        },
    }
    return await _submit_update(
        sessions,
        bot_id=bot_id,
        user_id=user_id,
        payload=payload,
        at=at,
        assistant=assistant,
    )


async def _send_callback(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    callback_data: str,
    at: datetime,
) -> tuple[UUID, JobClaim, dict[str, object]]:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "callback_query": {
            "id": f"local-callback-{uuid4().hex}",
            "from": {"id": user_id, "is_bot": False, "first_name": "Fixture"},
            "message": {"message_id": 1, "chat": {"id": user_id, "type": "private"}},
            "data": callback_data,
        },
    }
    return await _submit_update(
        sessions,
        bot_id=bot_id,
        user_id=user_id,
        payload=payload,
        at=at,
    )


def _callback_data(payload: dict[str, object], *, prefix: str, label: str | None = None) -> str:
    markup = payload.get("reply_markup")
    assert isinstance(markup, dict)
    rows = markup.get("inline_keyboard")
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, list)
        for button in row:
            assert isinstance(button, dict)
            callback = button.get("callback_data")
            caption = button.get("text")
            label_matches = (
                label is None
                or (isinstance(caption, str) and label.casefold() in caption.casefold())
            )
            if (
                isinstance(callback, str)
                and callback.startswith(prefix)
                and label_matches
            ):
                return callback
    raise AssertionError(f"no owner-bound button found for prefix {prefix!r}, label {label!r}")


async def _drain_delivery(
    sessions: async_sessionmaker[AsyncSession],
    transport: _FakeTelegramTransport,
    *,
    at: datetime,
) -> None:
    for _ in range(100):
        processed = await process_delivery_iteration(
            sessions,
            transport,
            worker_id="issue25-core-fake-telegram",
            now=lambda: at,
        )
        if not processed:
            return
    raise AssertionError("delivery worker did not become idle within the bounded drain")


async def _run_application_text(
    sessions: async_sessionmaker[AsyncSession],
    transport: _FakeTelegramTransport,
    *,
    bot_id: int,
    user_id: int,
    value: str,
    at: datetime,
) -> tuple[UUID, JobClaim, dict[str, object]]:
    result = await _send_text(
        sessions, bot_id=bot_id, user_id=user_id, text_value=value, at=at
    )
    await _drain_delivery(sessions, transport, at=at)
    return result


async def _schedule_accountability(
    sessions: async_sessionmaker[AsyncSession],
    transport: _FakeTelegramTransport,
    *,
    at: datetime,
) -> None:
    async with sessions() as session, session.begin():
        await schedule_accountability_iteration(session, transport, at=at)


@pytest.mark.parametrize("language", ["en", "ru"])
def test_private_core_lifecycle_runs_through_real_producers_and_consumers(
    core_lifecycle_database: str,
    language: str,
    record_property: Callable[[str, str], None],
) -> None:
    async def scenario() -> None:
        engine = create_engine(core_lifecycle_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC).replace(microsecond=0)
        clock = _Clock(at)
        suffix = uuid4().int % 800_000_000
        bot_id = BOT_BASE + suffix
        user_id = USER_BASE + suffix
        delivery = _FakeTelegramTransport(sessions)
        owner_id: UUID | None = None
        try:
            # A real persisted private inbox starts and completes onboarding. No profile,
            # Billing account, plan, occurrence, intent, or Accountability cycle is seeded.
            start_owner, _, _ = await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value="/start",
                at=clock.now(),
            )
            owner_id = start_owner
            language_answer = "en" if language == "en" else "ru"
            await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value=language_answer,
                at=clock.now(),
            )
            await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value="Asia/Almaty",
                at=clock.now(),
            )
            suggested = "suggested" if language == "en" else "по умолчанию"
            await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value=suggested,
                at=clock.now(),
            )
            await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value=suggested,
                at=clock.now(),
            )
            confirmed_word = "confirm" if language == "en" else "подтверждаю"
            await _run_application_text(
                sessions,
                delivery,
                bot_id=bot_id,
                user_id=user_id,
                value=confirmed_word,
                at=clock.now(),
            )
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                onboarding = await session.scalar(
                    select(OnboardingRecord).where(OnboardingRecord.owner_id == owner_id)
                )
                assert profile is not None and profile.language == language
                assert profile.home_zone == "Asia/Almaty"
                assert profile.reminder_calendar_days == 1
                assert profile.reminder_elapsed_hours == 1
                assert onboarding is not None and onboarding.step == "complete"

            # One deterministic model proposal intentionally omits appointment duration.
            # The production capture path must ask, bind a choice to the owner/draft, and
            # remain unsaved until a separate confirmation callback arrives.
            zone = ZoneInfo("Asia/Almaty")
            local_now = clock.now().astimezone(zone)
            appointment_date = local_now.date() + timedelta(days=3)
            start_local = datetime.combine(appointment_date, time(15, 0))
            title = "Quarterly review" if language == "en" else "Квартальный обзор"
            request_text = (
                f"Schedule {title} for {appointment_date.isoformat()} at 15:00"
                if language == "en"
                else f"Запланируй {title} на {appointment_date.isoformat()} в 15:00"
            )
            interpreter_transport = _DeterministicInterpreterTransport(
                _provider_response(
                    {
                        "kind": "proposal",
                        "language": language,
                        "operation": "propose_create_plan",
                        "fields": {
                            "title": title,
                            "schedule": {
                                "kind": "appointment",
                                "local_start": start_local.isoformat(timespec="minutes"),
                                "timezone": "Asia/Almaty",
                            },
                        },
                    }
                )
            )
            interpreter = BoundedPlanningAssistant(
                transport=interpreter_transport,
                usage=SqlAlchemyProviderUsageRecorder(sessions),
                allowance=SqlAlchemyTurnAllowance(sessions),
                now=clock.now,
            )
            _, capture_claim, clarification = await _send_text(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                text_value=request_text,
                at=clock.now(),
                assistant=interpreter,
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert interpreter_transport.calls == 1
            clarification_marker = (
                "How long"
                if language == "en"
                else "\u0421\u043a\u043e\u043b\u044c\u043a\u043e"
            )
            assert clarification_marker in str(clarification["plain_text"])
            choice_prefix = "capq:"
            duration_label = "30 minutes" if language == "en" else "30 минут"
            duration_choice = _callback_data(
                clarification,
                prefix=choice_prefix,
                label=duration_label,
            )
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0
                assert await session.get(Job, capture_claim.job_id) is not None

            _, _, preview = await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=duration_choice,
                at=clock.now(),
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert title in str(preview["plain_text"])
            confirm_data = _callback_data(preview, prefix="capc:")
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0
                assert await session.scalar(
                    select(func.count()).select_from(OccurrenceRecord).where(
                        OccurrenceRecord.owner_id == owner_id
                    )
                ) == 0

            clock.at += timedelta(seconds=2)
            _, _, confirmation = await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=confirm_data,
                at=clock.now(),
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert interpreter_transport.calls == 1
            assert str(confirmation["plain_text"]).strip()

            async with sessions() as session:
                plans = list(
                    await session.scalars(
                        select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                    )
                )
                occurrences = list(
                    await session.scalars(
                        select(OccurrenceRecord).where(OccurrenceRecord.owner_id == owner_id)
                    )
                )
                rules = list(
                    await session.scalars(
                        select(ReminderRuleRecord).where(
                            ReminderRuleRecord.owner_id == owner_id
                        )
                    )
                )
            assert len(plans) == len(occurrences) == 1
            plan_id, occurrence_id = plans[0].id, occurrences[0].id
            assert occurrences[0].plan_item_id == plan_id
            assert occurrences[0].kind == "appointment"
            assert occurrences[0].starts_at is not None
            assert occurrences[0].ends_at is not None
            assert occurrences[0].ends_at - occurrences[0].starts_at == timedelta(minutes=30)
            assert len(rules) == 2

            # Replaying the already-consumed owner-bound confirmation token cannot create
            # another plan, occurrence, reminder rule, or provider call.
            _, _, stale_reply = await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=confirm_data,
                at=clock.now(),
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(OccurrenceRecord).where(
                        OccurrenceRecord.owner_id == owner_id
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(ReminderRuleRecord).where(
                        ReminderRuleRecord.owner_id == owner_id
                    )
                ) == 2
            assert interpreter_transport.calls == 1
            assert stale_reply["handling"] == "handled"

            # Consume the real Planning outbox with its Notifications consumer, then
            # dispatch both approved reminder offsets through the actual delivery worker.
            assert await process_pending_reminder_schedules(
                sessions, now=clock.now(), limit=1000
            ) >= 1
            async with sessions() as session:
                reminders = list(
                    await session.scalars(
                        select(ReminderIntentRecord)
                        .where(ReminderIntentRecord.owner_id == owner_id)
                        .order_by(ReminderIntentRecord.due_at)
                    )
                )
            assert len(reminders) == 2
            assert {row.occurrence_id for row in reminders} == {occurrence_id}
            assert all(row.state == "scheduled" for row in reminders)
            reminder_due_times = sorted(row.due_at for row in reminders)
            assert reminder_due_times[0] == occurrences[0].starts_at - timedelta(days=1)
            assert reminder_due_times[1] == occurrences[0].starts_at - timedelta(hours=1)

            for due_at in reminder_due_times:
                clock.at = due_at
                before = len(delivery.reminders)
                await _drain_delivery(sessions, delivery, at=clock.now())
                assert len(delivery.reminders) == before + 1
            appointment_end = occurrences[0].ends_at
            assert appointment_end is not None

            # The actual Planning event consumer opens the appointment cycle at its saved
            # end. The delivery worker confirms the initial check, and its persisted result
            # is consumed before the one-hour nudge becomes due.
            clock.at = appointment_end
            await _schedule_accountability(sessions, delivery, at=clock.now())
            before_replies = len(delivery.replies)
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert len(delivery.replies) == before_replies + 1
            initial_check = delivery.replies[-1][1]
            initial_text = str(getattr(initial_check, "plain_text", ""))
            assert title in initial_text
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.initial_confirmed_at == appointment_end
                assert cycle.nudge_due_at == appointment_end + timedelta(hours=1)
                initial_attempts = list(
                    await session.scalars(
                        select(AccountabilityDeliveryAttemptRecord)
                        .join(
                            AccountabilityIntentRecord,
                            AccountabilityIntentRecord.id
                            == AccountabilityDeliveryAttemptRecord.intent_id,
                        )
                        .where(AccountabilityIntentRecord.cycle_id == cycle.id)
                    )
                )
                assert len(initial_attempts) == 1
                assert initial_attempts[0].outcome == "confirmed"

            # Consume the durable delivery-outcome event, then make exactly one nudge due.
            await _schedule_accountability(sessions, delivery, at=clock.now())
            clock.at = appointment_end + timedelta(hours=1)
            await _schedule_accountability(sessions, delivery, at=clock.now())
            before_replies = len(delivery.replies)
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert len(delivery.replies) == before_replies + 1
            nudge_message = delivery.replies[-1][1]
            ac_data = _callback_data(
                {
                    "reply_markup": getattr(nudge_message, "reply_markup", None)
                },
                prefix="ac:",
            )
            async with sessions() as session:
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None and cycle.nudge_slot_used
                assert cycle.state == "exhausted"
                intents = list(
                    await session.scalars(
                        select(AccountabilityIntentRecord).where(
                            AccountabilityIntentRecord.owner_id == owner_id,
                            AccountabilityIntentRecord.cycle_id == cycle.id,
                        )
                    )
                )
                assert sum(row.slot == "initial" for row in intents) == 1
                assert sum(row.slot == "nudge" for row in intents) == 1

            # The nudge action is itself an owner-bound inbox callback. It produces a Done
            # preview; a second callback confirms the mutation through the application worker.
            _, _, done_preview = await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=ac_data,
                at=clock.now(),
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert title in str(done_preview["plain_text"])
            done_confirm = _callback_data(
                done_preview,
                prefix=MUTATION_CALLBACK_PREFIX,
                label="confirm" if language == "en" else "подтвердить",
            )
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert occurrence is not None and occurrence.state == "open"

            clock.at += timedelta(seconds=2)
            _, _, done_result = await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=done_confirm,
                at=clock.now(),
            )
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert str(done_result["plain_text"]).strip()

            # Consume resulting planning events and sweep due work. The one occurrence
            # stays completed, its one-nudge cycle stays exhausted, and no send stays open.
            assert await process_pending_reminder_schedules(
                sessions, now=clock.now(), limit=1000
            ) >= 0
            await _schedule_accountability(sessions, delivery, at=clock.now())
            await _drain_delivery(sessions, delivery, at=clock.now())
            replies_before_future_sweep = len(delivery.replies)
            clock.at = appointment_end + timedelta(hours=24)
            assert await process_pending_reminder_schedules(
                sessions, now=clock.now(), limit=1000
            ) >= 0
            await _schedule_accountability(sessions, delivery, at=clock.now())
            await _drain_delivery(sessions, delivery, at=clock.now())
            assert len(delivery.replies) == replies_before_future_sweep
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                cycle = await session.scalar(
                    select(CheckInCycleRecord).where(
                        CheckInCycleRecord.owner_id == owner_id,
                        CheckInCycleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert cycle is not None
                intents = list(
                    await session.scalars(
                        select(ReminderIntentRecord).where(
                            ReminderIntentRecord.owner_id == owner_id
                        )
                    )
                )
                accountability_intents = list(
                    await session.scalars(
                        select(AccountabilityIntentRecord).where(
                            AccountabilityIntentRecord.owner_id == owner_id,
                            AccountabilityIntentRecord.cycle_id == cycle.id,
                        )
                    )
                )
                outstanding_jobs = list(
                    await session.scalars(
                        select(Job).where(
                            Job.owner_id == owner_id,
                            Job.lane == JobLane.DELIVERY.value,
                            Job.status.in_({"pending", "leased"}),
                        )
                    )
                )
                assert occurrence is not None and occurrence.id == occurrence_id
                assert occurrence.plan_item_id == plan_id
                assert occurrence.state == "completed"
                assert cycle is not None and cycle.state == "exhausted"
                assert all(row.state != "scheduled" for row in intents)
                assert {row.slot for row in accountability_intents} == {"initial", "nudge"}
                assert all(
                    row.state not in {"scheduled", "sending"}
                    for row in accountability_intents
                )
                assert outstanding_jobs == []
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(OccurrenceRecord).where(
                        OccurrenceRecord.owner_id == owner_id
                    )
                ) == 1

            record_property("plan_id", str(plan_id))
            record_property("occurrence_id", str(occurrence_id))
            record_property("language", language)
            record_property(
                "local_fake_reminder_attempts", json.dumps([x[0] for x in delivery.reminders])
            )
            record_property(
                "local_fake_reply_attempts", json.dumps([x[0] for x in delivery.replies])
            )
            record_property("final_plan_count", "1")
            record_property("final_occurrence_count", "1")
            record_property("final_occurrence_state", "completed")
            record_property("final_outstanding_delivery_jobs", "0")
            record_property("future_accountability_sweep_replies", "0")
        finally:
            await engine.dispose()

    run_local_async(scenario)
