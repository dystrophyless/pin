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


async def _seed_owner(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=BOT_ID, telegram_user_id=USER_ID
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
                title="Prepare quarterly review",
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
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID, text: str
) -> UUID:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "message": {
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
            "chat": {"id": USER_ID, "type": "private"},
            "date": int(NOW.timestamp()),
            "text": text,
        },
    }
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=BOT_ID,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == BOT_ID,
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
    sessions: async_sessionmaker[AsyncSession], *, owner_id: UUID, callback_data: str
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{uuid4().hex}",
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
            "message": {"message_id": 1, "chat": {"id": USER_ID, "type": "private"}},
            "data": callback_data,
        },
    }
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=BOT_ID,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == BOT_ID,
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


async def _reply(
    sessions: async_sessionmaker[AsyncSession], claim: JobClaim
) -> dict[str, object]:
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


def test_owned_advice_selected_steps_clarification_preview_and_confirm(
    support_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(support_database)
        sessions = create_session_factory(engine)
        try:
            owner_id = await _seed_owner(sessions)
            before: int
            async with sessions() as session:
                before = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
            assert before == 1

            advice_transport = _Transport(
                _completion(
                    {
                        "kind": "advice",
                        "language": "en",
                        "summary": "Start with the upcoming review.",
                        "steps": [
                            {
                                "title": "Gather meeting notes",
                                "reason": "The saved review is approaching.",
                            },
                            {
                                "title": "Draft the agenda",
                                "reason": "The notes will make it concrete.",
                            },
                        ],
                    },
                    "issue17-advice",
                )
            )
            await _send_text(
                sessions, owner_id=owner_id, text="What should I do next?"
            )
            advice_claim = await _process_next(sessions, _assistant(sessions, advice_transport))
            advice_payload = await _reply(sessions, advice_claim)
            assert "Gather meeting notes" in str(advice_payload["plain_text"])
            serialized_context = str(advice_transport.payloads[0]["messages"])
            assert "Prepare quarterly review" in serialized_context
            assert "2026-10-16" in serialized_context
            assert "free time" not in str(advice_payload["plain_text"]).casefold()
            async with sessions() as session:
                after_advice = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
                saved_draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert saved_draft is not None and saved_draft.state == "collecting"
                assert saved_draft.preview is None
                assert saved_draft.normalized_actions in (None, [])
                assert "What should I do next?" in str(saved_draft.messages)
            assert after_advice == before

            clarification_transport = _Transport(
                _completion(
                    {
                        "kind": "reply",
                        "language": "en",
                        "text": "I have the two selected steps. What date should I use?",
                    },
                    "issue17-clarification",
                )
            )
            await _send_text(sessions, owner_id=owner_id, text="Add steps 1 and 2")
            clarification_claim = await _process_next(
                sessions, _assistant(sessions, clarification_transport)
            )
            clarification_payload = await _reply(sessions, clarification_claim)
            assert "What date should I use?" in str(clarification_payload["plain_text"])
            prior_history = str(clarification_transport.payloads[0]["messages"])
            assert "1. Gather meeting notes" in prior_history
            assert "2. Draft the agenda" in prior_history
            assert "Add steps 1 and 2" in prior_history
            async with sessions() as session:
                after_clarification = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
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
                        "language": "en",
                        "actions": [
                            {
                                "kind": "proposal",
                                "language": "en",
                                "operation": "propose_create_plan",
                                "fields": {
                                    "title": "Gather meeting notes",
                                    "description": "For the quarterly review",
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
                                "language": "en",
                                "operation": "propose_create_plan",
                                "fields": {
                                    "title": "Draft the agenda",
                                    "description": "For the quarterly review",
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
                    "issue17-selected",
                )
            )
            await _send_text(sessions, owner_id=owner_id, text="Friday October 16 at 9 and 10")
            preview_claim = await _process_next(sessions, _assistant(sessions, proposal_transport))
            preview_payload = await _reply(sessions, preview_claim)
            assert "Plan preview" in str(preview_payload["plain_text"])
            confirm_data = _confirm_data(preview_payload)
            assert len(confirm_data.encode("utf-8")) <= 64
            preview_history = str(proposal_transport.payloads[0]["messages"])
            assert "1. Gather meeting notes" in preview_history
            assert "Add steps 1 and 2" in preview_history
            async with sessions() as session:
                before_confirmation = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "ready" and draft.preview is not None
                assert "Gather meeting notes" in str(preview_payload["plain_text"])
                assert "Draft the agenda" in str(preview_payload["plain_text"])
            assert before_confirmation == before

            await _send_callback(sessions, owner_id=owner_id, callback_data=confirm_data)
            confirmation_claim = await _process_next(sessions, assistant=None)
            confirmation_payload = await _reply(sessions, confirmation_claim)
            assert "saved" in str(confirmation_payload["plain_text"]).casefold()
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
                assert [plan.title for plan in plans] == [
                    "Draft the agenda",
                    "Gather meeting notes",
                    "Prepare quarterly review",
                ]
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
                        "language": "en",
                        "text": "Those selected times overlap. Which task should move?",
                    },
                    "issue17-conflict",
                )
            )
            await _send_text(
                sessions, owner_id=owner_id, text="Put both selected steps at 9 am"
            )
            conflict_claim = await _process_next(
                sessions, _assistant(sessions, conflict_transport)
            )
            conflict_payload = await _reply(sessions, conflict_claim)
            assert "Which task should move?" in str(conflict_payload["plain_text"])
            assert "created" not in str(conflict_payload["plain_text"]).casefold()
            assert len(conflict_transport.payloads) == 1
            async with sessions() as session:
                after_conflict = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
            assert after_conflict == before + 2

            exhausted_transport = _Transport()
            await _send_text(
                sessions, owner_id=owner_id, text="What should I do with the next plan?"
            )
            exhausted_claim = await _process_next(
                sessions, _assistant(sessions, exhausted_transport)
            )
            exhausted_payload = await _reply(sessions, exhausted_claim)
            assert exhausted_transport.payloads == []
            assert "not available" in str(exhausted_payload["plain_text"]).casefold()

            async with sessions() as session, session.begin():
                period = await session.scalar(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id
                    )
                )
                assert period is not None
                period.ends_at = NOW - timedelta(seconds=1)
            expired_transport = _Transport()
            await _send_text(
                sessions, owner_id=owner_id, text="Add another suggested step"
            )
            expired_claim = await _process_next(sessions, _assistant(sessions, expired_transport))
            expired_payload = await _reply(sessions, expired_claim)
            assert expired_transport.payloads == []
            assert "not available" in str(expired_payload["plain_text"]).casefold()
            async with sessions() as session:
                final_count = await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) or 0
                assert final_count == before + 2
        finally:
            await engine.dispose()

    run_local_async(scenario)

