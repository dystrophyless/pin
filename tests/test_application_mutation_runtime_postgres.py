from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.mutation_runtime_store import MutationRuntimePendingRecord
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.contexts.billing.application.contracts import StartTrial
from pin.contexts.billing.infrastructure.models import AccessPeriodRecord, UsageReservationRecord
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.infrastructure.capture_models import CaptureDraftRecord
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.infrastructure.models import ReminderIntentRecord
from pin.contexts.planning.domain.plans import OneOffPlanDraft, PlanKind, resolve_schedule
from pin.contexts.planning.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import SeriesRecord
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.persistence.models import ActionReceipt
from pin.platform.jobs import JobClaim, JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_app_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_APPLICATION_MUTATION_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_APPLICATION_MUTATION_DATABASE_URL to the assigned Issue 15 fixture"
        )
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = connection.exec_driver_sql("select current_database()").scalar_one()
            port = connection.exec_driver_sql("show port").scalar_one()
            user = connection.exec_driver_sql("select current_user").scalar_one()
            directory = connection.exec_driver_sql("show data_directory").scalar_one()
    finally:
        engine.dispose()
    if not str(name).startswith(DATABASE_PREFIX):
        pytest.fail("application mutation database is outside the assigned pin_issue15_app_ prefix")
    if int(port) != DATABASE_PORT or user != "pin_test":
        pytest.fail("application mutation PostgreSQL does not match the assigned user and port")
    if str(directory).replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("application mutation database is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def app_database_url() -> str:
    database_url = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return database_url


class _Transport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0
        self.payloads: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.payloads.append(payload)
        if self.calls > 1:
            raise AssertionError("mutation confirmation must not call the provider again")
        return self.response


def _provider_response(result: dict[str, object], request_id: str) -> dict[str, object]:
    return {
        "id": request_id,
        "model": ROUTE,
        "choices": [{"message": {"content": json.dumps(result)}}],
        "usage": {
            "prompt_tokens": 110,
            "completion_tokens": 24,
            "total_tokens": 134,
            "cost": "0.0000041",
        },
    }


def _assistant(
    sessions: async_sessionmaker[AsyncSession], transport: _Transport, at: datetime
) -> BoundedPlanningAssistant:
    return BoundedPlanningAssistant(
        transport=transport,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: at,
    )


async def _seed_owner(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    language: str,
    at: datetime,
) -> UUID:
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language=language,
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
        await SqlAlchemyAccessUsageStore(session).activate_trial(
            StartTrial(owner_id, telegram_user_id=user_id, at=at)
        )
        return owner_id


async def _seed_task(
    session: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    at: datetime,
    day: date,
    with_reminder: bool = False,
) -> tuple[UUID, UUID]:
    draft = OneOffPlanDraft(
        kind=PlanKind.TASK,
        title=title,
        local_date=day,
        local_time=time(12),
        event_zone="UTC",
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    plan_id, occurrence_id = uuid4(), uuid4()
    session.add(PlanItemRecord(id=plan_id, owner_id=owner_id, kind="task", title=title, version=1))
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
    if with_reminder:
        session.add(
            ReminderRuleRecord(
                id=uuid4(),
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind="elapsed_seconds",
                offset_amount=3600,
                source="explicit",
                due_at=at + timedelta(days=1),
            )
        )
    await session.flush()
    return plan_id, occurrence_id


async def _seed_appointment(
    session: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    at: datetime,
    day: date,
    representation: str,
    with_reminder: bool = True,
) -> tuple[UUID, UUID]:
    if representation == "duration":
        draft = OneOffPlanDraft(
            kind=PlanKind.APPOINTMENT,
            title=title,
            local_date=day,
            local_time=time(12),
            event_zone="UTC",
            duration_seconds=90 * 60,
        )
    else:
        draft = OneOffPlanDraft(
            kind=PlanKind.APPOINTMENT,
            title=title,
            local_date=day,
            local_time=time(12),
            event_zone="UTC",
            end_local_time=time(13, 30),
        )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    plan_id, occurrence_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(id=plan_id, owner_id=owner_id, kind="appointment", title=title, version=1)
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
    if with_reminder:
        assert schedule.starts_at is not None
        session.add(
            ReminderRuleRecord(
                id=uuid4(),
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind="elapsed_seconds",
                offset_amount=3600,
                source="explicit",
                due_at=schedule.starts_at - timedelta(hours=1),
            )
        )
    await session.flush()
    return plan_id, occurrence_id


async def _seed_weekly_series(
    session: AsyncSession, *, owner_id: UUID, at: datetime
) -> tuple[UUID, list[UUID]]:
    draft = RecurringPlanDraft(
        kind=PlanKind.TASK,
        title="Weekly integration test",
        start_date=date(2026, 10, 19),
        local_time=time(12),
        event_zone="UTC",
        recurrence=RecurrenceRule(RecurrenceFrequency.WEEKLY, interval=1, weekdays=(0,), count=3),
    )
    projection = project_recurrence(
        draft,
        owner_id=owner_id,
        default_zone="UTC",
        default_reminders=(),
        at=at,
        through=date(2026, 12, 1),
    )
    await SqlAlchemySeriesStore(session).persist_series(
        owner_id=owner_id,
        action_key=f"app-runtime-seed-{uuid4()}",
        payload_sha256="c" * 64,
        approved_preferences_version=1,
        draft=draft,
        projection=projection,
        effective_reminders=(),
        default_zone="UTC",
        quiet_zone="UTC",
        quiet_start=time(22),
        quiet_end=time(8),
        at=at,
        horizon_date=date(2026, 12, 1),
    )
    series = await session.scalar(select(SeriesRecord).where(SeriesRecord.owner_id == owner_id))
    assert series is not None
    occurrences = list(
        (
            await session.scalars(
                select(OccurrenceRecord)
                .where(OccurrenceRecord.owner_id == owner_id)
                .order_by(OccurrenceRecord.local_date)
            )
        ).all()
    )
    assert len(occurrences) == 3
    return series.id, [item.id for item in occurrences]


async def _persist_update(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    text: str | None,
    callback: str | None,
) -> tuple[UUID, UUID]:
    update_id = uuid4().int % 9_000_000_000_000_000
    if callback is None:
        payload: dict[str, object] = {
            "update_id": update_id,
            "message": {
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat": {"id": user_id, "type": "private"},
                "date": int(datetime.now(UTC).timestamp()),
                "text": text,
            },
        }
    else:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"app-callback-{uuid4().hex}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "message": {
                    "message_id": 1,
                    "chat": {"id": user_id, "type": "private"},
                },
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
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
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
        return inbox.owner_id, inbox.id


async def _process_update(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    at: datetime,
    text: str | None = None,
    callback: str | None = None,
    assistant: BoundedPlanningAssistant | None = None,
) -> tuple[JobClaim, dict[str, object]]:
    await _persist_update(sessions, bot_id=bot_id, user_id=user_id, text=text, callback=callback)
    claim = await claim_application_job(sessions, worker_id=f"app-mutation-{uuid4().hex[:12]}")
    assert claim is not None
    await process_application_job(sessions, claim, now=at, assistant=assistant)
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        saved_job = await session.get(Job, claim.job_id)
        assert saved_job is not None and saved_job.status == "succeeded"
        return claim, cast(dict[str, object], event.payload["payload"])


def _callback(payload: dict[str, object], *, prefix: str = "pm:") -> str:
    markup = cast(dict[str, object], payload["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    choices = [
        button["callback_data"]
        for row in rows
        for button in row
        if isinstance(button.get("callback_data"), str)
        and str(button["callback_data"]).startswith(prefix)
    ]
    assert choices
    return cast(str, choices[0])


def _all_callbacks(payload: dict[str, object]) -> list[str]:
    markup = cast(dict[str, object], payload["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    return [
        cast(str, button["callback_data"])
        for row in rows
        for button in row
        if isinstance(button.get("callback_data"), str)
    ]


def test_actual_application_done_menu_preview_confirm_and_stale_fences(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id = 6_000_000_000 + suffix
        owner_user = 7_000_000_000 + suffix
        foreign_user = 8_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=owner_user, language="en", at=at
            )
            foreign_owner = await _seed_owner(
                sessions, bot_id=bot_id, user_id=foreign_user, language="en", at=at
            )
            async with sessions() as session, session.begin():
                plan_id, target_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Complete through application job",
                    at=at,
                    day=date(2026, 10, 20),
                    with_reminder=True,
                )
                _, foreign_target_id = await _seed_task(
                    session,
                    owner_id=foreign_owner,
                    title="Private foreign task",
                    at=at,
                    day=date(2026, 10, 21),
                )

            # Seed the native reminder consumer from a committed schedule event.
            from pin.platform.contracts import EventEnvelope
            from pin.platform.events import add_outbox_event

            async with sessions() as session, session.begin():
                await add_outbox_event(
                    session,
                    EventEnvelope(
                        id=uuid4(),
                        event_type="planning.plan_created",
                        schema_version=1,
                        aggregate_id=target_id,
                        aggregate_version=1,
                        owner_id=owner_id,
                        occurred_at=at,
                        payload={"test_fixture": "committed_plan"},
                    ),
                )
            assert await process_pending_reminder_schedules(sessions, now=at) >= 1
            async with sessions() as session:
                intent = await session.scalar(
                    select(ReminderIntentRecord).where(
                        ReminderIntentRecord.owner_id == owner_id,
                        ReminderIntentRecord.occurrence_id == target_id,
                    )
                )
                assert intent is not None and intent.state == "scheduled"

            command_claim, menu = await _process_update(
                sessions, bot_id=bot_id, user_id=owner_user, at=at, text="/done"
            )
            markup = cast(dict[str, object], menu["reply_markup"])
            rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
            labels = [button["text"] for row in rows for button in row]
            assert any("Complete through application job" in str(label) for label in labels)
            assert all("Private foreign task" not in str(label) for label in labels)
            selection = _callback(menu)

            # A copied callback is safe for the other mapped Telegram owner.
            _, foreign_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=foreign_user,
                at=at,
                callback=selection,
            )
            assert (
                "changed" in str(foreign_reply["plain_text"]).lower()
                or "expired" in str(foreign_reply["plain_text"]).lower()
            )
            async with sessions() as session:
                foreign_target = await session.get(OccurrenceRecord, foreign_target_id)
                assert foreign_target is not None
                assert (foreign_target.state, foreign_target.version) == ("open", 1)

            _, preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=selection,
            )
            assert "Complete through application job" in str(preview["plain_text"])
            confirm = _callback(preview)
            async with sessions() as session:
                effect = await session.scalar(
                    select(ActionReceipt).where(
                        ActionReceipt.owner_id == owner_id,
                        ActionReceipt.action_key
                        == f"job:{command_claim.job_id}:private_text_applied",
                    )
                )
                assert effect is not None
                untouched = await session.get(OccurrenceRecord, target_id)
                assert untouched is not None and (untouched.state, untouched.version) == ("open", 1)

            _, completed = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=confirm,
            )
            assert "marked done" in str(completed["plain_text"]).lower()
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, target_id)
                assert occurrence is not None and (occurrence.state, occurrence.version) == (
                    "completed",
                    2,
                )
                completed_event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "planning.occurrence_completed",
                        OutboxEvent.aggregate_id == target_id,
                        OutboxEvent.owner_id == owner_id,
                    )
                )
                assert completed_event is not None
                assert completed_event.aggregate_version == 2
            await process_pending_reminder_schedules(sessions, now=at + timedelta(seconds=1))
            async with sessions() as session:
                intent = await session.scalar(
                    select(ReminderIntentRecord).where(
                        ReminderIntentRecord.owner_id == owner_id,
                        ReminderIntentRecord.occurrence_id == target_id,
                    )
                )
                assert intent is not None and intent.state == "invalidated"
                active = await session.scalar(
                    select(func.count())
                    .select_from(ReminderIntentRecord)
                    .where(
                        ReminderIntentRecord.owner_id == owner_id,
                        ReminderIntentRecord.occurrence_id == target_id,
                        ReminderIntentRecord.state == "scheduled",
                    )
                )
                assert active == 0
                reservations = await session.scalar(
                    select(func.count())
                    .select_from(UsageReservationRecord)
                    .where(UsageReservationRecord.owner_id == owner_id)
                )
                assert reservations == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_actual_application_deterministic_recurring_reschedule_needs_date_scope_and_confirm(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 9_000_000_000 + suffix, 10_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="ru", at=at
            )
            async with sessions() as session, session.begin():
                _series_id, occurrence_ids = await _seed_weekly_series(
                    session, owner_id=owner_id, at=at
                )
            _, menu = await _process_update(
                sessions, bot_id=bot_id, user_id=user_id, at=at, text="/reschedule"
            )
            selection = _callback(menu)
            _, date_prompt = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=selection,
            )
            assert date_prompt.get("reply_markup") is not None
            assert len(_all_callbacks(date_prompt)) == 2
            _, schedule_prompt = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback(date_prompt),
            )
            assert schedule_prompt.get("reply_markup") is None
            _, preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="2026-10-22 13:30",
            )
            assert "2026-10-22 13:30" in str(preview["plain_text"])
            confirm = _callback(preview)
            async with sessions() as session:
                for occurrence_id in occurrence_ids:
                    occurrence = await session.get(OccurrenceRecord, occurrence_id)
                    assert occurrence is not None
                    assert (occurrence.state, occurrence.version) == ("open", 1)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageReservationRecord)
                        .where(UsageReservationRecord.owner_id == owner_id)
                    )
                    == 0
                )
            _, saved = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm,
            )
            async with sessions() as session:
                anchor = await session.get(OccurrenceRecord, occurrence_ids[0])
                sibling = await session.get(OccurrenceRecord, occurrence_ids[1])
                assert anchor is not None, saved["plain_text"]
                assert (anchor.local_date, anchor.local_time, anchor.version) == (
                    date(2026, 10, 22),
                    time(13, 30),
                    2,
                )
                assert sibling is not None and (sibling.state, sibling.version) == ("open", 1)
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type.in_(
                            {
                                "planning.plan_rescheduled",
                                "planning.series_occurrence_rescheduled",
                            }
                        ),
                        OutboxEvent.owner_id == owner_id,
                        OutboxEvent.aggregate_id == occurrence_ids[0],
                    )
                )
                assert event is not None
                assert await session.scalar(
                    select(func.count())
                    .select_from(UsageReservationRecord)
                    .where(UsageReservationRecord.owner_id == owner_id)
                ) == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_validated_ai_mutation_confirms_once_and_correction_stales_old_token(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 11_000_000_000 + suffix, 12_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=at
            )
            async with sessions() as session, session.begin():
                _plan_id, target_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Quarterly control review",
                    at=at,
                    day=date(2026, 10, 20),
                )
            transport = _Transport(
                _provider_response(
                    {
                        "kind": "proposal",
                        "language": "en",
                        "operation": "propose_complete",
                        "target_id": str(target_id),
                        "expected_version": 1,
                        "fields": {},
                    },
                    f"app-mutation-{uuid4().hex}",
                )
            )
            assistant = _assistant(sessions, transport, at)
            _, proposal_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="Mark Quarterly control review complete",
                assistant=assistant,
            )
            assert transport.calls == 1
            assert "Quarterly control review" in str(proposal_reply["plain_text"])
            assert "Mark this occurrence done?" in str(proposal_reply["plain_text"])
            outbound = json.dumps(transport.payloads[0], ensure_ascii=False)
            assert str(target_id) in outbound
            assert "Quarterly control review" in outbound
            confirm = _callback(proposal_reply)
            async with sessions() as session:
                row = await session.get(OccurrenceRecord, target_id)
                assert row is not None and (row.state, row.version) == ("open", 1)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id
                    )
                )
                assert reservation is not None and reservation.state == "settled"
                calls = (
                    await session.scalars(
                        select(ProviderCallRecord).where(ProviderCallRecord.owner_id == owner_id)
                    )
                ).all()
                assert len(calls) == 1 and calls[0].state == "reported"

            # A new deterministic correction supersedes this preview; its old confirm cannot write.
            _, correction = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text=f"/cancel {target_id}",
            )
            stale_job, stale_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm,
            )
            assert "stale" in str(stale_reply["plain_text"]).lower()
            assert transport.calls == 1
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, target_id)
                assert occurrence is not None and (occurrence.state, occurrence.version) == (
                    "open",
                    1,
                )
                effect = await session.scalar(
                    select(ActionReceipt).where(
                        ActionReceipt.owner_id == owner_id,
                        ActionReceipt.action_key == f"job:{stale_job.job_id}:private_text_applied",
                    )
                )
                assert effect is not None
                correction_confirm = _callback(correction)
            _, cancelled = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=correction_confirm,
            )
            assert "cancelled" in str(cancelled["plain_text"]).lower()
            assert transport.calls == 1
            async with sessions() as session:
                occurrence = await session.get(OccurrenceRecord, target_id)
                assert occurrence is not None and (occurrence.state, occurrence.version) == (
                    "cancelled",
                    2,
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(CaptureDraftRecord)
                        .where(CaptureDraftRecord.owner_id == owner_id)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_actual_application_allows_terminal_actions_for_overdue_items_with_active_access(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 13_000_000_000 + suffix, 14_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=at
            )
            async with sessions() as session, session.begin():
                _, complete_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Overdue completion",
                    at=at,
                    day=date(2026, 10, 20),
                )
                _, cancel_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Overdue cancellation",
                    at=at,
                    day=date(2026, 10, 20),
                )
                _, reschedule_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Overdue move denial",
                    at=at,
                    day=date(2026, 10, 20),
                )
                for occurrence_id in (complete_id, cancel_id, reschedule_id):
                    occurrence = await session.get(OccurrenceRecord, occurrence_id)
                    assert occurrence is not None
                    occurrence.local_date = date(2026, 10, 13)
                    occurrence.starts_at = datetime(2026, 10, 13, 12, tzinfo=UTC)

            _, done_preview = await _process_update(
                sessions, bot_id=bot_id, user_id=user_id, at=at, text=f"/done {complete_id}"
            )
            _, done_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback(done_preview),
            )
            assert "marked done" in str(done_reply["plain_text"]).lower()

            _, cancel_preview = await _process_update(
                sessions, bot_id=bot_id, user_id=user_id, at=at, text=f"/cancel {cancel_id}"
            )
            _, cancel_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback(cancel_preview),
            )
            assert "cancelled" in str(cancel_reply["plain_text"]).lower()

            _, date_prompt = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text=f"/reschedule {reschedule_id}",
            )
            assert "YYYY-MM-DD" in str(date_prompt["plain_text"])
            _, denied = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="2026-10-22 14:00",
            )
            assert "not changed" in str(denied["plain_text"]).lower()
            async with sessions() as session:
                completed = await session.get(OccurrenceRecord, complete_id)
                cancelled = await session.get(OccurrenceRecord, cancel_id)
                refused = await session.get(OccurrenceRecord, reschedule_id)
                assert completed is not None and completed.state == "completed"
                assert cancelled is not None and cancelled.state == "cancelled"
                assert refused is not None and (refused.state, refused.version) == ("open", 1)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageReservationRecord)
                        .where(UsageReservationRecord.owner_id == owner_id)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_actual_application_allows_terminal_actions_after_trial_expiry_but_denies_reschedule(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        started_at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 17_000_000_000 + suffix, 18_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=started_at
            )
            async with sessions() as session:
                period = await session.scalar(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == "trial",
                    )
                )
                assert period is not None and period.starts_at == started_at
                period_ends_at = period.ends_at
            expired_at = period_ends_at + timedelta(seconds=1)
            live_preview_at = period_ends_at - timedelta(minutes=5)
            target_day = expired_at.date() + timedelta(days=2)

            async with sessions() as session, session.begin():
                _, done_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Expired trial completion",
                    at=expired_at,
                    day=target_day,
                )
                _, cancel_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Expired trial cancellation",
                    at=expired_at,
                    day=target_day,
                )
                _, denied_at_preview_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Expired trial reschedule preview denial",
                    at=expired_at,
                    day=target_day,
                )
                _, denied_at_confirm_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Reschedule expires before confirmation",
                    at=live_preview_at,
                    day=target_day,
                )

            # The preview is valid inside the persisted trial period, but the
            # confirmation callback arrives just after that exact stored expiry.
            await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=live_preview_at,
                text=f"/reschedule {denied_at_confirm_id}",
            )
            _, live_preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=live_preview_at,
                text=f"{target_day.isoformat()} 14:00",
            )
            assert "Reschedule" in str(live_preview["plain_text"])
            live_confirm_token = _callback(live_preview)
            _, expired_confirmation = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                callback=live_confirm_token,
            )
            assert "access expired" in str(expired_confirmation["plain_text"]).lower()
            assert "nothing was changed" in str(expired_confirmation["plain_text"]).lower()
            token_secret = live_confirm_token.removeprefix("pm:")
            confirm_hash = hashlib.sha256(token_secret.encode("ascii")).hexdigest()
            async with sessions() as session:
                confirm_binding = await session.get(TelegramCallbackTokenRecord, confirm_hash)
                assert confirm_binding is not None
                assert confirm_binding.owner_id == owner_id
                assert confirm_binding.action == "mutation.confirm"
                assert confirm_binding.consumed_at is None
                stale_pending = await session.scalar(
                    select(MutationRuntimePendingRecord)
                    .where(
                        MutationRuntimePendingRecord.owner_id == owner_id,
                        MutationRuntimePendingRecord.occurrence_id == denied_at_confirm_id,
                        MutationRuntimePendingRecord.action == "reschedule",
                    )
                    .order_by(MutationRuntimePendingRecord.created_at.desc())
                    .limit(1)
                )
                assert stale_pending is not None and stale_pending.state == "stale"

            # A new reschedule request initiated after trial expiry cannot get
            # a mutation preview, even for a future open occurrence.
            async with sessions() as session:
                confirm_token_count_before = await session.scalar(
                    select(func.count()).select_from(TelegramCallbackTokenRecord).where(
                        TelegramCallbackTokenRecord.owner_id == owner_id,
                        TelegramCallbackTokenRecord.action == "mutation.confirm",
                    )
                )
            await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                text=f"/reschedule {denied_at_preview_id}",
            )
            _, expired_preview_denial = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                text=f"{target_day.isoformat()} 15:00",
            )
            assert "could not prepare a current preview" in str(
                expired_preview_denial["plain_text"]
            ).lower()
            assert "not changed" in str(expired_preview_denial["plain_text"]).lower()
            async with sessions() as session:
                confirm_token_count_after = await session.scalar(
                    select(func.count()).select_from(TelegramCallbackTokenRecord).where(
                        TelegramCallbackTokenRecord.owner_id == owner_id,
                        TelegramCallbackTokenRecord.action == "mutation.confirm",
                    )
                )
                assert confirm_token_count_after == confirm_token_count_before

            _, done_preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                text=f"/done {done_id}",
            )
            _, done_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                callback=_callback(done_preview),
            )
            assert "marked done" in str(done_reply["plain_text"]).lower()

            _, cancel_preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                text=f"/cancel {cancel_id}",
            )
            _, cancel_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=expired_at,
                callback=_callback(cancel_preview),
            )
            assert "cancelled" in str(cancel_reply["plain_text"]).lower()

            async with sessions() as session:
                persisted_period = await session.scalar(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == "trial",
                    )
                )
                assert persisted_period is not None
                assert persisted_period.ends_at == period_ends_at
                assert expired_at > persisted_period.ends_at
                done = await session.get(OccurrenceRecord, done_id)
                cancelled = await session.get(OccurrenceRecord, cancel_id)
                denied_preview = await session.get(OccurrenceRecord, denied_at_preview_id)
                denied_confirm = await session.get(OccurrenceRecord, denied_at_confirm_id)
                assert done is not None and (done.state, done.version) == ("completed", 2)
                assert cancelled is not None and (cancelled.state, cancelled.version) == (
                    "cancelled",
                    2,
                )
                for refused in (denied_preview, denied_confirm):
                    assert refused is not None and (refused.state, refused.version) == ("open", 1)
                    assert refused.local_date == target_day
                    assert refused.local_time == time(12)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageReservationRecord)
                        .where(UsageReservationRecord.owner_id == owner_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ProviderCallRecord)
                        .where(ProviderCallRecord.owner_id == owner_id)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize("representation", ["duration", "explicit_end"])
def test_application_reschedule_preserves_appointment_duration_and_reminders(
    app_database_url: str, representation: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 15_000_000_000 + suffix, 16_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=at
            )
            async with sessions() as session, session.begin():
                plan_id, occurrence_id = await _seed_appointment(
                    session,
                    owner_id=owner_id,
                    title=f"{representation} appointment",
                    at=at,
                    day=date(2026, 10, 20),
                    representation=representation,
                )

            _, menu = await _process_update(
                sessions, bot_id=bot_id, user_id=user_id, at=at, text="/reschedule"
            )
            _, prompt = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback(menu),
            )
            assert "YYYY-MM-DD HH:MM" in str(prompt["plain_text"])
            _, preview = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="2026-10-22 13:30",
            )
            assert "2026-10-22 13:30" in str(preview["plain_text"])
            confirm = _callback(preview)
            async with sessions() as session:
                untouched = await session.get(OccurrenceRecord, occurrence_id)
                assert untouched is not None
                assert (untouched.local_date, untouched.local_time, untouched.duration_seconds) == (
                    date(2026, 10, 20),
                    time(12),
                    90 * 60,
                )

            _, confirmed = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm,
            )
            assert "rescheduled" in str(confirmed["plain_text"]).lower()
            async with sessions() as session:
                plan = await session.get(PlanItemRecord, plan_id)
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert plan is not None and (plan.title, plan.version) == (
                    f"{representation} appointment",
                    1,
                )
                assert occurrence is not None
                assert (
                    occurrence.state,
                    occurrence.version,
                    occurrence.local_date,
                    occurrence.local_time,
                    occurrence.end_local_date,
                    occurrence.end_local_time,
                    occurrence.starts_at,
                    occurrence.ends_at,
                    occurrence.duration_seconds,
                ) == (
                    "open",
                    2,
                    date(2026, 10, 22),
                    time(13, 30),
                    None,
                    None,
                    datetime(2026, 10, 22, 13, 30, tzinfo=UTC),
                    datetime(2026, 10, 22, 15, tzinfo=UTC),
                    90 * 60,
                )
                reminder = await session.scalar(
                    select(ReminderRuleRecord).where(
                        ReminderRuleRecord.owner_id == owner_id,
                        ReminderRuleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert reminder is not None and (
                    reminder.offset_kind,
                    reminder.offset_amount,
                    reminder.source,
                ) == ("elapsed_seconds", 3600, "explicit")
                changed = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.owner_id == owner_id,
                        OutboxEvent.aggregate_id == occurrence_id,
                        OutboxEvent.aggregate_version == 2,
                        OutboxEvent.event_type.in_(
                            {"planning.plan_rescheduled", "planning.series_occurrence_rescheduled"}
                        ),
                    )
                )
                assert changed is not None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_application_model_omissions_preserve_appointment_duration_and_reminder(
    app_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 17_000_000_000 + suffix, 18_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=at
            )
            async with sessions() as session, session.begin():
                plan_id, occurrence_id = await _seed_appointment(
                    session,
                    owner_id=owner_id,
                    title="Omitted fields appointment",
                    at=at,
                    day=date(2026, 10, 20),
                    representation="duration",
                )
            transport = _Transport(
                _provider_response(
                    {
                        "kind": "proposal",
                        "language": "en",
                        "operation": "propose_change_occurrence",
                        "target_id": str(occurrence_id),
                        "expected_version": 1,
                        "fields": {
                            "schedule": {
                                "kind": "appointment",
                                "local_start": "2026-10-22T13:30:00",
                                "timezone": "UTC",
                            }
                        },
                    },
                    f"app-mutation-{uuid4().hex}",
                )
            )
            assistant = _assistant(sessions, transport, at)
            _, proposal_reply = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="Move Omitted fields appointment to October 22 at 1:30 PM",
                assistant=assistant,
            )
            assert transport.calls == 1
            assert "2026-10-22 13:30" in str(proposal_reply["plain_text"])
            confirm = _callback(proposal_reply)
            _, confirmed = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm,
                assistant=assistant,
            )
            assert "rescheduled" in str(confirmed["plain_text"]).lower()
            assert transport.calls == 1
            async with sessions() as session:
                plan = await session.get(PlanItemRecord, plan_id)
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert plan is not None and (plan.title, plan.version) == (
                    "Omitted fields appointment",
                    1,
                )
                assert occurrence is not None and (
                    occurrence.local_date,
                    occurrence.local_time,
                    occurrence.end_local_date,
                    occurrence.end_local_time,
                    occurrence.starts_at,
                    occurrence.ends_at,
                    occurrence.duration_seconds,
                ) == (
                    date(2026, 10, 22),
                    time(13, 30),
                    None,
                    None,
                    datetime(2026, 10, 22, 13, 30, tzinfo=UTC),
                    datetime(2026, 10, 22, 15, tzinfo=UTC),
                    90 * 60,
                )
                reminder = await session.scalar(
                    select(ReminderRuleRecord).where(
                        ReminderRuleRecord.owner_id == owner_id,
                        ReminderRuleRecord.occurrence_id == occurrence_id,
                    )
                )
                assert reminder is not None and (
                    reminder.offset_kind,
                    reminder.offset_amount,
                    reminder.source,
                ) == ("elapsed_seconds", 3600, "explicit")
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize("explicit_null_field", ["local_end", "duration_minutes"])
def test_application_explicit_null_appointment_end_clarifies_without_plan_write(
    app_database_url: str, explicit_null_field: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(app_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 19_000_000_000 + suffix, 20_000_000_000 + suffix
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language="en", at=at
            )
            async with sessions() as session, session.begin():
                plan_id, occurrence_id = await _seed_appointment(
                    session,
                    owner_id=owner_id,
                    title="Must retain appointment",
                    at=at,
                    day=date(2026, 10, 20),
                    representation="explicit_end",
                )
            schedule: dict[str, object] = {
                "kind": "appointment",
                "local_start": "2026-10-22T13:30:00",
                "timezone": "UTC",
                explicit_null_field: None,
            }
            transport = _Transport(
                _provider_response(
                    {
                        "kind": "proposal",
                        "language": "en",
                        "operation": "propose_change_occurrence",
                        "target_id": str(occurrence_id),
                        "expected_version": 1,
                        "fields": {"schedule": schedule},
                    },
                    f"app-mutation-{uuid4().hex}",
                )
            )
            assistant = _assistant(sessions, transport, at)
            _, clarification = await _process_update(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                text="Move Must retain appointment to October 22 at 1:30 PM",
                assistant=assistant,
            )
            assert transport.calls == 1
            assert clarification.get("reply_markup") is None
            assert "appointment" in str(clarification["plain_text"]).lower()
            async with sessions() as session:
                plan = await session.get(PlanItemRecord, plan_id)
                occurrence = await session.get(OccurrenceRecord, occurrence_id)
                assert plan is not None and (plan.title, plan.version) == (
                    "Must retain appointment",
                    1,
                )
                assert occurrence is not None and (
                    occurrence.state,
                    occurrence.version,
                    occurrence.local_date,
                    occurrence.local_time,
                    occurrence.end_local_date,
                    occurrence.end_local_time,
                    occurrence.duration_seconds,
                ) == (
                    "open",
                    1,
                    date(2026, 10, 20),
                    time(12),
                    date(2026, 10, 20),
                    time(13, 30),
                    90 * 60,
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)
