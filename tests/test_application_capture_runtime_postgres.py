from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.application.contracts import CallStart, PlanningAssistant
from pin.contexts.conversation.application.dto import (
    ConversationContext,
    ModelFailure,
    ModelSuccess,
    Reply,
)
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.domain.proposals import (
    FailureCategory,
    InputKind,
    SourceLanguage,
)
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
from pin.contexts.planning.infrastructure.models import PlanItemRecord
from pin.persistence import run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.platform.jobs import JobClaim, JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue14_runtime_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


def _runtime_database_url() -> str:
    database_url = os.getenv("PIN_TEST_CAPTURE_RUNTIME_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_CAPTURE_RUNTIME_DATABASE_URL to the assigned runtime fixture")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            database_name = connection.exec_driver_sql("select current_database()").scalar_one()
            port = connection.exec_driver_sql("show port").scalar_one()
            data_directory = connection.exec_driver_sql("show data_directory").scalar_one()
    finally:
        engine.dispose()
    if not str(database_name).startswith(DATABASE_PREFIX):
        pytest.fail("runtime PostgreSQL database name is outside the assigned disposable prefix")
    if int(port) != DATABASE_PORT:
        pytest.fail("runtime PostgreSQL port does not match the assigned fixture")
    if str(data_directory).replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail("runtime PostgreSQL data directory is not the assigned disposable cluster")
    return database_url


@pytest.fixture(scope="session")
def capture_runtime_database() -> Iterator[str]:
    database_url = _runtime_database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    yield database_url


class _FakeTransport:
    def __init__(self, *responses: dict[str, object] | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if not self.responses:
            raise AssertionError("runtime tried to submit an extra provider request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _provider_response(result: dict[str, object], request_id: str) -> dict[str, object]:
    return {
        "id": request_id,
        "model": ROUTE,
        "choices": [{"message": {"content": json.dumps(result)}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 35,
            "total_tokens": 155,
            "cost": "0.0000073",
        },
    }


def _assistant(
    sessions: async_sessionmaker[AsyncSession], transport: _FakeTransport, at: datetime
) -> BoundedPlanningAssistant:
    return BoundedPlanningAssistant(
        transport=transport,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: at,
    )


async def _seed_profile(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    telegram_user_id: int,
    at: datetime,
) -> UUID:
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=telegram_user_id
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
                period_id=f"runtime-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="runtime-test-v1",
                starts_at=at - timedelta(days=365),
                ends_at=at + timedelta(days=365),
                ai_turn_limit=100,
                voice_second_limit=900,
            )
        )
        return owner_id


async def _send_private_text(
    sessions: async_sessionmaker[AsyncSession], *, bot_id: int, user_id: int, text: str
) -> tuple[UUID, UUID, int]:
    update_id = uuid4().int % 9_000_000_000_000_000
    payload: dict[str, object] = {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": user_id, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
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
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        await _promote_latest_pending(session, inbox.owner_id)
        return inbox.owner_id, inbox.id, update_id


async def _send_callback(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    callback_data: str,
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
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        await _promote_latest_pending(session, inbox.owner_id)


async def _promote_latest_pending(session: AsyncSession, owner_id: UUID) -> None:
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


async def _process_next(
    sessions: async_sessionmaker[AsyncSession],
    *,
    at: datetime,
    assistant: PlanningAssistant | None,
) -> JobClaim:
    claim = await claim_application_job(sessions, worker_id=f"capture-runtime-{uuid4().hex[:12]}")
    assert claim is not None
    await process_application_job(
        sessions,
        claim,
        now=at,
        assistant=assistant,
    )
    return claim


async def _reply_payload(
    sessions: async_sessionmaker[AsyncSession], *, claim: JobClaim
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


def _callback_with_prefix(payload: dict[str, object], prefix: str) -> str:
    markup = cast(dict[str, object], payload["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    callback_data = [
        button["callback_data"]
        for row in rows
        for button in row
        if isinstance(button.get("callback_data"), str)
        and str(button["callback_data"]).startswith(prefix)
    ]
    assert callback_data
    return cast(str, callback_data[0])


def test_completed_profile_text_runs_through_fenced_capture_runtime(
    capture_runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_runtime_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC)
        identity_suffix = uuid4().int % 1_000_000_000
        telegram_user_id = 1_000_000_000 + identity_suffix
        bot_id = 2_000_000_000 + identity_suffix
        try:
            payload = {
                "update_id": int(at.timestamp() * 1_000_000),
                "message": {
                    "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": telegram_user_id, "type": "private"},
                    "date": int(at.timestamp()),
                    "text": "Organize the garage tomorrow",
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
                        InboxUpdate.update_id == payload["update_id"],
                    )
                )
                assert inbox is not None and inbox.owner_id is not None
                owner_id = inbox.owner_id
                inbox_id = inbox.id
                await _promote_latest_pending(session, owner_id)
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

            claim = await claim_application_job(sessions, worker_id="capture-runtime-pg-test")
            assert claim is not None

            class Assistant:
                calls = 0

                async def interpret(
                    self,
                    context: ConversationContext,
                    *,
                    at: datetime,
                    claim: JobClaim | None = None,
                ) -> ModelSuccess:
                    self.calls += 1
                    assert context.owner_id == owner_id
                    assert context.request_id == f"telegram-inbox:{inbox_id}"
                    assert "Current user turn:\nOrganize the garage tomorrow" in context.text
                    assert context.reference_date == at.astimezone(ZoneInfo("Asia/Almaty")).date()
                    return ModelSuccess(
                        result=Reply(
                            kind="reply",
                            language=SourceLanguage.EN,
                            text="What should I organize first?",
                        ),
                        provider_request_ids=("runtime-test-request",),
                    )

            assistant = Assistant()
            await process_application_job(sessions, claim, now=at, assistant=assistant)
            assert assistant.calls == 1

            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "collecting"
                assert draft.processing_key is None
                assert draft.messages[-1]["text"] == "What should I organize first?"
                saved_input = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert saved_input is not None and saved_input.state == "complete"
                saved_job = await session.get(Job, claim.job_id)
                assert saved_job is not None and saved_job.status == "succeeded"
                reply_event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "telegram.reply.requested",
                        OutboxEvent.causation_id == claim.job_id,
                    )
                )
                assert reply_event is not None
                event_payload = reply_event.payload["payload"]
                assert event_payload["chat_id"] == telegram_user_id
                assert "What should I organize first?" in event_payload["plain_text"]
                assert event_payload["source_job_id"] == str(claim.job_id)
                assert event_payload["page_index"] == 0
                assert event_payload["page_count"] == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_runtime_clarification_foreign_stale_and_confirmation_callbacks_are_fenced(
    capture_runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_runtime_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id = 3_000_000_000 + suffix
        user_id = 4_000_000_000 + suffix
        foreign_user_id = 5_000_000_000 + suffix
        text = "Organize the garage tomorrow"
        request_key: str
        try:
            owner_id = await _seed_profile(
                sessions, bot_id=bot_id, telegram_user_id=user_id, at=at
            )
            foreign_owner_id = await _seed_profile(
                sessions, bot_id=bot_id, telegram_user_id=foreign_user_id, at=at
            )
            received_owner_id, inbox_id, _ = await _send_private_text(
                sessions, bot_id=bot_id, user_id=user_id, text=text
            )
            assert received_owner_id == owner_id
            request_key = f"telegram-inbox:{inbox_id}"
            transport = _FakeTransport(
                _provider_response(
                    {
                        "kind": "proposal",
                        "language": "en",
                        "operation": "propose_create_plan",
                        "fields": {
                            "title": "Organize garage",
                            "schedule": {
                                "kind": "task",
                                "local_date": (at.astimezone(ZoneInfo("Asia/Almaty")).date()
                                + timedelta(days=1)).isoformat(),
                                "timing": "unspecified",
                                "timezone": "Asia/Almaty",
                            },
                        },
                    },
                    f"runtime-preview-{uuid4().hex}",
                )
            )
            assistant = _assistant(sessions, transport, at)
            claim = await claim_application_job(sessions, worker_id="capture-runtime-preview")
            assert claim is not None and claim.owner_id == owner_id

            # Simulate a crash after the bounded assistant durably recorded its normalized result
            # but before the application worker persisted the capture reply.
            replay_context = ConversationContext(
                owner_id=owner_id,
                request_id=request_key,
                language=SourceLanguage.EN,
                input_kind=InputKind.TEXT,
                text=f"Current user turn:\n{text}",
                reference_date=at.astimezone(ZoneInfo("Asia/Almaty")).date(),
                timezone="Asia/Almaty",
            )
            precomputed = await assistant.interpret(replay_context, at=at, claim=claim)
            assert isinstance(precomputed, ModelSuccess)
            assert transport.calls == 1
            await process_application_job(sessions, claim, now=at, assistant=assistant)
            assert transport.calls == 1

            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "collecting"
                assert draft.preview is None
                assert draft.normalized_actions
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == request_key,
                    )
                )
                assert reservation is not None and reservation.state == "settled"
                assert reservation.settled_ai_turns == 1

            first_reply = await _reply_payload(sessions, claim=claim)
            choice_data = _callback_with_prefix(first_reply, "capq:")

            # A token copied to a different Telegram owner cannot read or change the draft.
            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=foreign_user_id,
                callback_data=choice_data,
            )
            foreign_choice_claim = await _process_next(sessions, at=at, assistant=assistant)
            foreign_reply = await _reply_payload(sessions, claim=foreign_choice_claim)
            assert "changed or expired" in str(foreign_reply["plain_text"])
            assert "Organize garage" not in str(foreign_reply["plain_text"])
            assert transport.calls == 1
            async with sessions() as session:
                assert await session.scalar(
                    select(CaptureDraftRecord).where(
                        CaptureDraftRecord.owner_id == foreign_owner_id
                    )
                ) is None
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0

            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=choice_data,
            )
            choice_claim = await _process_next(sessions, at=at, assistant=assistant)
            ready_reply = await _reply_payload(sessions, claim=choice_claim)
            confirm_data = _callback_with_prefix(ready_reply, "capc:")
            assert "capx:" in str(ready_reply["reply_markup"])
            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "ready"
                assert draft.preview is not None
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0
                call = await session.scalar(
                    select(ProviderCallRecord).where(
                        ProviderCallRecord.owner_id == owner_id,
                        ProviderCallRecord.turn_id == request_key,
                    )
                )
                assert call is not None and call.state == "reported"

            # The same consumed choice token cannot apply a second time.
            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=choice_data,
            )
            stale_choice_claim = await _process_next(sessions, at=at, assistant=assistant)
            stale_reply = await _reply_payload(sessions, claim=stale_choice_claim)
            assert "changed or expired" in str(stale_reply["plain_text"])
            assert transport.calls == 1

            # A stolen confirmation is owner-scoped too and does not create a plan.
            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=foreign_user_id,
                callback_data=confirm_data,
            )
            foreign_confirm_claim = await _process_next(sessions, at=at, assistant=assistant)
            foreign_confirm_reply = await _reply_payload(sessions, claim=foreign_confirm_claim)
            assert "changed or expired" in str(foreign_confirm_reply["plain_text"])
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0

            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=confirm_data,
            )
            confirm_claim = await _process_next(sessions, at=at, assistant=assistant)
            saved_reply = await _reply_payload(sessions, claim=confirm_claim)
            assert "Saved" in str(saved_reply["plain_text"])
            assert transport.calls == 1
            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "confirmed"
                plans = await session.scalars(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
                saved_plans = list(plans)
                assert len(saved_plans) == 1
                assert saved_plans[0].title == "Organize garage"

            # Replaying the confirmation callback returns safely without duplicating the plan.
            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=confirm_data,
            )
            duplicate_confirm_claim = await _process_next(sessions, at=at, assistant=assistant)
            duplicate_reply = await _reply_payload(sessions, claim=duplicate_confirm_claim)
            assert str(duplicate_reply["plain_text"]).strip()
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_runtime_cancel_callback_is_deterministic_and_saves_nothing(
    capture_runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_runtime_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id = 6_000_000_000 + suffix
        user_id = 7_000_000_000 + suffix
        try:
            owner_id = await _seed_profile(
                sessions, bot_id=bot_id, telegram_user_id=user_id, at=at
            )
            received_owner_id, inbox_id, _ = await _send_private_text(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                text="Help me organize the garage",
            )
            assert received_owner_id == owner_id
            transport = _FakeTransport(
                _provider_response(
                    {
                        "kind": "reply",
                        "language": "en",
                        "text": "What kind of organization do you have in mind?",
                    },
                    f"runtime-cancel-{uuid4().hex}",
                )
            )
            assistant = _assistant(sessions, transport, at)
            first_claim = await _process_next(sessions, at=at, assistant=assistant)
            first_reply = await _reply_payload(sessions, claim=first_claim)
            cancel_data = _callback_with_prefix(first_reply, "capx:")
            assert transport.calls == 1

            await _send_callback(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                callback_data=cancel_data,
            )
            cancel_claim = await _process_next(sessions, at=at, assistant=assistant)
            cancel_reply = await _reply_payload(sessions, claim=cancel_claim)
            assert "cancelled" in str(cancel_reply["plain_text"]).lower()
            assert transport.calls == 1

            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                assert draft is not None and draft.state == "cancelled"
                assert await session.scalar(
                    select(func.count()).select_from(PlanItemRecord).where(
                        PlanItemRecord.owner_id == owner_id
                    )
                ) == 0
                reservation_count = await session.scalar(
                    select(func.count()).select_from(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert reservation_count == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_runtime_unknown_provider_attempt_is_never_resubmitted(
    capture_runtime_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_runtime_database)
        sessions = create_session_factory(engine)
        at = datetime.now(UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id = 8_000_000_000 + suffix
        user_id = 9_000_000_000 + suffix
        text = "Plan a garage cleanup"
        try:
            owner_id = await _seed_profile(
                sessions, bot_id=bot_id, telegram_user_id=user_id, at=at
            )
            received_owner_id, inbox_id, _ = await _send_private_text(
                sessions, bot_id=bot_id, user_id=user_id, text=text
            )
            assert received_owner_id == owner_id
            request_key = f"telegram-inbox:{inbox_id}"
            transport = _FakeTransport(TimeoutError("injected provider timeout"))
            assistant = _assistant(sessions, transport, at)
            await _process_next(sessions, at=at, assistant=assistant)
            assert transport.calls == 1

            context = ConversationContext(
                owner_id=owner_id,
                request_id=request_key,
                language=SourceLanguage.EN,
                input_kind=InputKind.TEXT,
                text=f"Current user turn:\n{text}",
                reference_date=at.astimezone(ZoneInfo("Asia/Almaty")).date(),
                timezone="Asia/Almaty",
            )
            replay = await assistant.interpret(context, at=at)
            assert isinstance(replay, ModelFailure)
            assert replay.category is FailureCategory.USAGE_UNKNOWN
            assert transport.calls == 1

            async with sessions() as session:
                call = await session.scalar(
                    select(ProviderCallRecord).where(
                        ProviderCallRecord.owner_id == owner_id,
                        ProviderCallRecord.turn_id == request_key,
                    )
                )
                assert call is not None and call.state == "unknown"
                receipt = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == request_key,
                    )
                )
                assert receipt is not None and receipt.state == "failed"

            # A persisted started call with an unsettled reservation is also ambiguous.
            # Replaying its request key reports unknown and never contacts the provider.
            started_key = f"runtime-started:{uuid4()}"
            allowance = SqlAlchemyTurnAllowance(sessions)
            usage = SqlAlchemyProviderUsageRecorder(sessions)
            assert (
                await allowance.reserve(
                    owner_id=owner_id,
                    request_id=started_key,
                    at=at,
                )
            ).value == "reserved"
            await usage.start_call(
                CallStart(
                    owner_id=owner_id,
                    turn_id=started_key,
                    call_id=uuid4(),
                    call_number=1,
                    model=ROUTE,
                    created_at=at,
                )
            )
            started_transport = _FakeTransport()
            started_assistant = _assistant(sessions, started_transport, at)
            started_context = ConversationContext(
                owner_id=owner_id,
                request_id=started_key,
                language=SourceLanguage.EN,
                input_kind=InputKind.TEXT,
                text="Current user turn:\nStart a garage cleanup",
                reference_date=at.astimezone(ZoneInfo("Asia/Almaty")).date(),
                timezone="Asia/Almaty",
            )
            started_replay = await started_assistant.interpret(started_context, at=at)
            assert isinstance(started_replay, ModelFailure)
            assert started_replay.category is FailureCategory.USAGE_UNKNOWN
            assert started_transport.calls == 0
            await allowance.refund(owner_id=owner_id, request_id=started_key, at=at)
            async with sessions() as session:
                started_call = await session.scalar(
                    select(ProviderCallRecord).where(
                        ProviderCallRecord.owner_id == owner_id,
                        ProviderCallRecord.turn_id == started_key,
                    )
                )
                assert started_call is not None and started_call.state == "started"
        finally:
            await engine.dispose()

    run_local_async(scenario)
