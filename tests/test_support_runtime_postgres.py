from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import claim_application_job, process_application_job
from pin.adapters.telegram.capture import PlanningAssistant
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.presentation.capture import CAPTURE_CONFIRM_PREFIX
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureDraftRecord,
    CaptureInputRecord,
)
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.persistence import run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.platform.jobs import JobClaim, JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
DATABASE_PREFIX = "pin_issue17_"
NOW = datetime(2026, 10, 14, 8, tzinfo=UTC)
BOT_ID = 71_700_000
USER_ID = 71_700_001


def _database_url() -> str:
    value = os.getenv("PIN_TEST_SUPPORT_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_SUPPORT_DATABASE_URL to a fresh Issue 17 fixture")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            data_directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX):
        pytest.fail("Issue 17 proof requires its own pin_issue17_ database")
    if port != DATABASE_PORT:
        pytest.fail("Issue 17 proof is outside the authorized PostgreSQL port")
    if data_directory.replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail("Issue 17 proof is outside the authorized PostgreSQL data directory")
    return value


@pytest.fixture(scope="session")
def support_database() -> Iterator[str]:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    yield value


class _Transport:
    def __init__(self, *results: dict[str, object]) -> None:
        self.results = list(results)
        self.payloads: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        if not self.results:
            raise AssertionError("model made an unexpected extra request")
        return self.results.pop(0)


def _completion(result: dict[str, object], request_id: str) -> dict[str, object]:
    return {
        "id": request_id,
        "model": ROUTE,
        "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
        "usage": {
            "prompt_tokens": 128,
            "completion_tokens": 64,
            "total_tokens": 192,
            "cost": "0.0000073",
        },
    }


def _assistant(
    sessions: async_sessionmaker[AsyncSession], transport: _Transport
) -> BoundedPlanningAssistant:
    return BoundedPlanningAssistant(
        transport=transport,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: NOW,
    )


async def _seed_owner(
    sessions: async_sessionmaker[AsyncSession], *, bot_id: int, user_id: int, language: str
) -> UUID:
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language=language,
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
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"support-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="issue17-support-v1",
                starts_at=NOW - timedelta(days=1),
                ends_at=NOW + timedelta(days=30),
                ai_turn_limit=4,
                voice_second_limit=900,
            )
        )
        plan_id = uuid4()
        session.add(
            PlanItemRecord(
                id=plan_id,
                owner_id=owner_id,
                kind="appointment",
                title=(
                    "Подготовить квартальный обзор"
                    if language == "ru"
                    else "Prepare quarterly review"
                ),
                version=1,
                created_at=NOW,
            )
        )
        await session.flush()
        session.add(
            OccurrenceRecord(
                id=uuid4(),
                owner_id=owner_id,
                plan_item_id=plan_id,
                kind="appointment",
                event_zone="Asia/Almaty",
                local_date=date(2026, 10, 16),
                local_time=time(15),
                fold=None,
                end_local_date=date(2026, 10, 16),
                end_local_time=time(16),
                end_fold=None,
                starts_at=datetime(2026, 10, 16, 9, tzinfo=UTC),
                ends_at=datetime(2026, 10, 16, 10, tzinfo=UTC),
                deadline_at=None,
                duration_seconds=3600,
                state="open",
                version=1,
                reminder_preferences_version=1,
                created_at=NOW,
            )
        )
        return owner_id


async def _send_text(
    sessions: async_sessionmaker[AsyncSession],
    *,
    owner_id: UUID,
    text: str,
    bot_id: int = BOT_ID,
    user_id: int = USER_ID,
) -> UUID:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": user_id, "type": "private"},
            "date": int(NOW.timestamp()),
            "text": text,
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
        assert inbox is not None and inbox.owner_id == owner_id
        job = await session.scalar(
            select(Job)
            .where(
                Job.owner_id == owner_id,
                Job.lane == JobLane.APPLICATION.value,
                Job.status == "pending",
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        assert job is not None
        job.priority = 2_147_483_647
        return inbox.id


async def _send_callback(
    sessions: async_sessionmaker[AsyncSession],
    *,
    owner_id: UUID,
    callback_data: str,
    bot_id: int = BOT_ID,
    user_id: int = USER_ID,
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{uuid4().hex}",
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "message": {"message_id": 1, "chat": {"id": user_id, "type": "private"}},
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
        assert inbox is not None and inbox.owner_id == owner_id
        job = await session.scalar(
            select(Job)
            .where(Job.owner_id == owner_id, Job.status == "pending")
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        assert job is not None
        job.priority = 2_147_483_647


async def _process_next(
    sessions: async_sessionmaker[AsyncSession], assistant: PlanningAssistant | None
) -> JobClaim:
    claim = await claim_application_job(sessions, worker_id=f"issue17-{uuid4().hex[:12]}")
    assert claim is not None
    assert claim.lane is JobLane.APPLICATION
    await process_application_job(sessions, claim, now=NOW, assistant=assistant)
    return claim


async def _reply(sessions: async_sessionmaker[AsyncSession], claim: JobClaim) -> dict[str, object]:
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        return cast(dict[str, object], event.payload["payload"])


def _confirm_data(payload: dict[str, object]) -> str:
    markup = cast(dict[str, object], payload["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    for row in rows:
        for button in row:
            value = button.get("callback_data")
            if isinstance(value, str) and value.startswith(CAPTURE_CONFIRM_PREFIX):
                return value
    raise AssertionError("preview did not publish an opaque confirm callback")


@pytest.mark.parametrize(
    ("language", "bot_id", "user_id"),
    [("en", BOT_ID, USER_ID), ("ru", BOT_ID + 1, USER_ID + 1)],
    ids=["english", "russian"],
)
def test_owned_advice_selected_steps_clarification_preview_and_confirm(
    support_database: str, language: str, bot_id: int, user_id: int
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(support_database)
        sessions = create_session_factory(engine)
        try:
            owner_id = await _seed_owner(
                sessions, bot_id=bot_id, user_id=user_id, language=language
            )
            if language == "ru":
                base_title = "Подготовить квартальный обзор"
                first_title, first_reason = "Собрать заметки к встрече", "Скоро состоится обзор."
                second_title, second_reason = (
                    "Составить повестку",
                    "Заметки помогут сделать её конкретной.",
                )
                advice_summary = "Начните с предстоящего обзора."
                advice_input = "Что мне делать дальше?"
                selection_input = "Добавь шаги 1 и 2"
                clarification_text = "Я выбрал оба шага. На какую дату их поставить?"
                date_input = "В пятницу, 16 октября, в 9 и 10"
                conflict_input = "Поставь оба выбранных шага на 9 утра"
                conflict_text = "Время шагов совпадает. Какой из них перенести?"
                exhausted_input = "Что делать со следующим планом?"
                expired_input = "Добавь ещё один предложенный шаг"
            else:
                base_title = "Prepare quarterly review"
                first_title, first_reason = (
                    "Gather meeting notes",
                    "The saved review is approaching.",
                )
                second_title, second_reason = "Draft the agenda", "The notes will make it concrete."
                advice_summary = "Start with the upcoming review."
                advice_input = "What should I do next?"
                selection_input = "Add steps 1 and 2"
                clarification_text = "I have the two selected steps. What date should I use?"
                date_input = "Friday October 16 at 9 and 10"
                conflict_input = "Put both selected steps at 9 am"
                conflict_text = "Those selected times overlap. Which task should move?"
                exhausted_input = "What should I do with the next plan?"
                expired_input = "Add another suggested step"
            before: int
            async with sessions() as session:
                before = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
            assert before == 1

            advice_transport = _Transport(
                _completion(
                    {
                        "kind": "advice",
                        "language": language,
                        "summary": advice_summary,
                        "steps": [
                            {
                                "title": first_title,
                                "reason": first_reason,
                            },
                            {
                                "title": second_title,
                                "reason": second_reason,
                            },
                        ],
                    },
                    f"issue17-{language}-advice",
                )
            )
            await _send_text(
                sessions, owner_id=owner_id, text=advice_input, bot_id=bot_id, user_id=user_id
            )
            advice_claim = await _process_next(sessions, _assistant(sessions, advice_transport))
            advice_payload = await _reply(sessions, advice_claim)
            assert first_title in str(advice_payload["plain_text"])
            assert advice_summary in str(advice_payload["plain_text"])
            serialized_context = str(advice_transport.payloads[0]["messages"])
            assert base_title in serialized_context
            assert "2026-10-16" in serialized_context
            ungrounded_time_claim = "свобод" if language == "ru" else "free time"
            assert ungrounded_time_claim not in str(advice_payload["plain_text"]).casefold()
            async with sessions() as session:
                after_advice = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
                saved_draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert saved_draft is not None and saved_draft.state == "collecting"
                assert saved_draft.preview is None
                assert saved_draft.normalized_actions in (None, [])
                assert advice_input in str(saved_draft.messages)
                preferences = await session.scalar(
                    select(PreferencesRecord).where(PreferencesRecord.owner_id == owner_id)
                )
                assert preferences is not None and preferences.language == language
            assert after_advice == before

            clarification_transport = _Transport(
                _completion(
                    {
                        "kind": "reply",
                        "language": language,
                        "text": clarification_text,
                    },
                    f"issue17-{language}-clarification",
                )
            )
            await _send_text(
                sessions, owner_id=owner_id, text=selection_input, bot_id=bot_id, user_id=user_id
            )
            clarification_claim = await _process_next(
                sessions, _assistant(sessions, clarification_transport)
            )
            clarification_payload = await _reply(sessions, clarification_claim)
            assert clarification_text in str(clarification_payload["plain_text"])
            prior_history = str(clarification_transport.payloads[0]["messages"])
            assert f"1. {first_title}" in prior_history
            assert f"2. {second_title}" in prior_history
            assert selection_input in prior_history
            async with sessions() as session:
                after_clarification = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "collecting" and draft.preview is None
            assert after_clarification == before

            proposal_transport = _Transport(
                _completion(
                    {
                        "kind": "proposal_batch",
                        "schema_version": 1,
                        "language": language,
                        "actions": [
                            {
                                "kind": "proposal",
                                "language": language,
                                "operation": "propose_create_plan",
                                "fields": {
                                    "title": first_title,
                                    "description": advice_summary,
                                    "schedule": {
                                        "kind": "task",
                                        "local_date": "2026-10-16",
                                        "local_time": "09:00:00",
                                        "timezone": "Asia/Almaty",
                                        "timing": "timed",
                                    },
                                    "reminders": [],
                                },
                            },
                            {
                                "kind": "proposal",
                                "language": language,
                                "operation": "propose_create_plan",
                                "fields": {
                                    "title": second_title,
                                    "description": advice_summary,
                                    "schedule": {
                                        "kind": "task",
                                        "local_date": "2026-10-16",
                                        "local_time": "10:00:00",
                                        "timezone": "Asia/Almaty",
                                        "timing": "timed",
                                    },
                                    "reminders": [],
                                },
                            },
                        ],
                    },
                    f"issue17-{language}-selected",
                )
            )
            await _send_text(
                sessions, owner_id=owner_id, text=date_input, bot_id=bot_id, user_id=user_id
            )
            preview_claim = await _process_next(sessions, _assistant(sessions, proposal_transport))
            preview_payload = await _reply(sessions, preview_claim)
            assert first_title in str(preview_payload["plain_text"])
            confirm_data = _confirm_data(preview_payload)
            assert len(confirm_data.encode("utf-8")) <= 64
            preview_history = str(proposal_transport.payloads[0]["messages"])
            assert f"1. {first_title}" in preview_history
            assert selection_input in preview_history
            assert date_input in preview_history
            async with sessions() as session:
                before_confirmation = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "ready" and draft.preview is not None
                assert first_title in str(preview_payload["plain_text"])
                assert second_title in str(preview_payload["plain_text"])
            assert before_confirmation == before

            await _send_callback(
                sessions,
                owner_id=owner_id,
                callback_data=confirm_data,
                bot_id=bot_id,
                user_id=user_id,
            )
            confirmation_claim = await _process_next(sessions, assistant=None)
            confirmation_payload = await _reply(sessions, confirmation_claim)
            saved_label = "сохран" if language == "ru" else "saved"
            assert saved_label in str(confirmation_payload["plain_text"]).casefold()
            async with sessions() as session:
                plans = list(
                    (
                        await session.scalars(
                            select(PlanItemRecord)
                            .where(PlanItemRecord.owner_id == owner_id)
                            .order_by(PlanItemRecord.title)
                        )
                    ).all()
                )
                assert len(plans) == before + 2
                assert [plan.title for plan in plans] == sorted(
                    [base_title, first_title, second_title]
                )
                calls = list(
                    (
                        await session.scalars(
                            select(ProviderCallRecord).where(
                                ProviderCallRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert len(calls) == 3
                assert all(call.state == "reported" for call in calls)
                assert all(call.cost_usd is not None and call.cost_usd > 0 for call in calls)
                reservations = list(
                    (
                        await session.scalars(
                            select(UsageReservationRecord).where(
                                UsageReservationRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert len(reservations) == 3
                assert all(item.state == "settled" for item in reservations)
                inputs = list(
                    (
                        await session.scalars(
                            select(CaptureInputRecord).where(
                                CaptureInputRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert all(item.state == "complete" for item in inputs)

            conflict_transport = _Transport(
                _completion(
                    {
                        "kind": "reply",
                        "language": language,
                        "text": conflict_text,
                    },
                    f"issue17-{language}-conflict",
                )
            )
            await _send_text(
                sessions,
                owner_id=owner_id,
                text=conflict_input,
                bot_id=bot_id,
                user_id=user_id,
            )
            conflict_claim = await _process_next(sessions, _assistant(sessions, conflict_transport))
            conflict_payload = await _reply(sessions, conflict_claim)
            assert conflict_text in str(conflict_payload["plain_text"])
            created_label = "создан" if language == "ru" else "created"
            assert created_label not in str(conflict_payload["plain_text"]).casefold()
            assert len(conflict_transport.payloads) == 1
            async with sessions() as session:
                after_conflict = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
            assert after_conflict == before + 2

            exhausted_transport = _Transport()
            await _send_text(
                sessions,
                owner_id=owner_id,
                text=exhausted_input,
                bot_id=bot_id,
                user_id=user_id,
            )
            exhausted_claim = await _process_next(
                sessions, _assistant(sessions, exhausted_transport)
            )
            exhausted_payload = await _reply(sessions, exhausted_claim)
            assert exhausted_transport.payloads == []
            unavailable_label = "недоступ" if language == "ru" else "not available"
            assert unavailable_label in str(exhausted_payload["plain_text"]).casefold()

            async with sessions() as session, session.begin():
                period = await session.scalar(
                    select(AccessPeriodRecord).where(AccessPeriodRecord.owner_id == owner_id)
                )
                assert period is not None
                period.ends_at = NOW - timedelta(seconds=1)
            expired_transport = _Transport()
            await _send_text(
                sessions,
                owner_id=owner_id,
                text=expired_input,
                bot_id=bot_id,
                user_id=user_id,
            )
            expired_claim = await _process_next(sessions, _assistant(sessions, expired_transport))
            expired_payload = await _reply(sessions, expired_claim)
            assert expired_transport.payloads == []
            assert unavailable_label in str(expired_payload["plain_text"]).casefold()
            async with sessions() as session:
                final_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner_id)
                    )
                    or 0
                )
                assert final_count == before + 2
        finally:
            await engine.dispose()

    run_local_async(scenario)
