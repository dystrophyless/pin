from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select

from pin.adapters.telegram.capture import TranscribedCapture, continue_transcribed_capture
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.contexts.billing.api import (
    Operation,
    ReservationState,
    ReserveUsage,
    ResizeVoiceUsage,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.conversation.application.contracts import CallStart, ReservationStatus
from pin.contexts.conversation.application.dto import (
    ConversationContext,
    ModelFailure,
    ModelSuccess,
)
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.domain.proposals import FailureCategory, InputKind, SourceLanguage
from pin.contexts.conversation.infrastructure.capture_models import CaptureDraftRecord
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.planning.infrastructure.models import PlanItemRecord
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.contexts.speech.infrastructure.store import SqlAlchemySpeechRequestStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobLane, LeaseLost, claim_job
from pin.platform.models import InboxUpdate, Job, OutboxEvent


@pytest.fixture(scope="module")
def prepared_voice_database():
    database_url = os.getenv("PIN_TEST_PREPARED_VOICE_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_PREPARED_VOICE_DATABASE_URL to the owned issue-18 fixture")
    engine = create_sync_engine(database_url)
    try:
        with engine.connect() as connection:
            assert str(
                connection.exec_driver_sql("select current_database()").scalar_one()
            ).startswith("pin_issue18_prepared_")
            assert int(connection.exec_driver_sql("show port").scalar_one()) == 51017
            directory = connection.exec_driver_sql("show data_directory").scalar_one()
            assert str(directory).replace("\\", "/") == (
                "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
            )
    finally:
        engine.dispose()
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return database_url


class _Transport:
    def __init__(self):
        self.calls = 0

    async def complete(self, payload):
        self.calls += 1
        assert "transcript" in payload["messages"][1]["content"]
        return {
            "id": f"gen-prepared-{uuid4()}",
            "model": ROUTE,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"kind": "reply", "language": "en", "text": "What time tomorrow?"}
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 12,
                "total_tokens": 62,
                "cost": "0.000003",
            },
        }


async def _seed(sessions, now):
    bot_id, user_id = uuid4().int % 1_000_000 + 1, uuid4().int % 1_000_000_000 + 1
    resolver = PostgresTelegramOwnerResolver()
    async with sessions() as session, session.begin():
        owner = await resolver.resolve(session, bot_id=bot_id, telegram_user_id=user_id)
        session.add(AccessAccountRecord(owner_id=owner))
        session.add(
            AccessPeriodRecord(
                owner_id=owner,
                period_id="prepared-test",
                policy_kind="trial",
                policy_version="v1",
                starts_at=now - timedelta(minutes=1),
                ends_at=now + timedelta(days=7),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            PreferencesRecord(
                owner_id=owner,
                language="en",
                home_zone="Asia/Almaty",
                reminder_calendar_days=1,
                reminder_elapsed_hours=2,
                quiet_start=time(22),
                quiet_end=time(8),
                review_time=time(20),
                version=1,
            )
        )
        session.add(
            OnboardingRecord(
                owner_id=owner,
                draft_id=uuid4(),
                mode="initial",
                step="complete",
                draft={},
                base_profile_version=1,
                version=1,
            )
        )
        update_id = uuid4().int % 9_000_000_000
        await persist_update(
            session,
            bot_id=bot_id,
            owner_resolver=resolver,
            payload={
                "update_id": update_id,
                "message": {
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": user_id, "type": "private"},
                    "date": int(now.timestamp()),
                    "voice": {
                        "file_id": "fixture-voice",
                        "file_unique_id": "fixture",
                        "duration": 2,
                        "file_size": 100,
                    },
                },
            },
        )
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        assert inbox is not None
        inbox_id = inbox.id
    async with sessions() as session, session.begin():
        claim = await claim_job(
            session,
            lane=JobLane.SPEECH,
            worker_id=f"test-{uuid4()}",
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None and claim.owner_id == owner
        key = f"telegram-inbox:{inbox_id}"
        billing = SqlAlchemyAccessUsageStore(session)
        result = await billing.reserve_usage(
            ReserveUsage(
                owner, key, Operation.VOICE_TRANSCRIPTION, 1, 2, now, now + timedelta(minutes=5)
            )
        )
        assert result.created
        await billing.resize_voice_usage(ResizeVoiceUsage(owner, key, 4, now))
    return owner, inbox_id, claim, key


def _context(owner, key, now):
    return ConversationContext(
        owner_id=owner,
        request_id=key,
        language=SourceLanguage.EN,
        input_kind=InputKind.TRANSCRIPT,
        text="Schedule a meeting tomorrow",
        reference_date=date(2026, 10, 14),
        timezone="Asia/Almaty",
    )


def _assistant(sessions, transport, now):
    return BoundedPlanningAssistant(
        transport=transport,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: now,
    )


def test_prepared_voice_reuses_and_settles_combined_hold_once(prepared_voice_database):
    async def run():
        engine = create_engine(prepared_voice_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        try:
            owner, _, claim, key = await _seed(sessions, now)
            transport = _Transport()
            assistant = _assistant(sessions, transport, now).with_allowance(
                SqlAlchemyTurnAllowance(sessions, voice_seconds=4)
            )
            outcome = await assistant.interpret(_context(owner, key, now), at=now, claim=claim)
            assert isinstance(outcome, ModelSuccess)
            replay = await assistant.interpret(_context(owner, key, now), at=now, claim=claim)
            assert isinstance(replay, ModelSuccess) and transport.calls == 1
            async with sessions() as session:
                row = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(owner, key)
                assert row.state is ReservationState.SETTLED
                assert (row.ai_turns, row.voice_seconds) == (1, 4)
                assert (row.settled_ai_turns, row.settled_voice_seconds) == (1, 4)
            denied = await SqlAlchemyTurnAllowance(sessions, voice_seconds=3).reserve(
                owner_id=owner, request_id=key, at=now, claim=claim
            )
            assert denied is ReservationStatus.DENIED
        finally:
            await engine.dispose()

    run_local_async(run)


def test_started_prepared_model_call_is_not_resubmitted(prepared_voice_database):
    async def run():
        engine = create_engine(prepared_voice_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        try:
            owner, _, claim, key = await _seed(sessions, now)
            await SqlAlchemyProviderUsageRecorder(sessions).start_call(
                CallStart(owner, key, uuid4(), 1, ROUTE, now), claim=claim
            )
            transport = _Transport()
            prepared = SqlAlchemyTurnAllowance(sessions, voice_seconds=4)
            assistant = _assistant(sessions, transport, now).with_allowance(prepared)
            outcome = await assistant.interpret(_context(owner, key, now), at=now, claim=claim)
            assert isinstance(outcome, ModelFailure)
            assert outcome.category is FailureCategory.USAGE_UNKNOWN and transport.calls == 0
            async with sessions() as session:
                reservation = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(
                    owner, key
                )
                assert reservation is not None
                assert reservation.state is ReservationState.REFUNDED
                assert (reservation.ai_turns, reservation.voice_seconds) == (1, 4)
            with pytest.raises(LeaseLost):
                await prepared.reserve(
                    owner_id=owner, request_id=key, at=now, claim=replace(claim, token=uuid4())
                )
            assert (
                await prepared.reserve(owner_id=owner, request_id="missing", at=now, claim=claim)
                is ReservationStatus.DENIED
            )
        finally:
            await engine.dispose()

    run_local_async(run)


def test_capture_and_speech_completion_commit_atomically_after_replay(prepared_voice_database):
    async def run():
        engine = create_engine(prepared_voice_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        try:
            owner, inbox_id, claim, key = await _seed(sessions, now)
            async with sessions() as session, session.begin():
                session.add(
                    SpeechRequestRecord(
                        job_id=claim.job_id,
                        owner_id=owner,
                        inbox_id=inbox_id,
                        request_key=key,
                        state="transcribed",
                        declared_size_bytes=100,
                        declared_duration_seconds=2,
                        actual_size_bytes=100,
                        decoded_duration_seconds=3.5,
                        transcript="Schedule a meeting tomorrow",
                        language="en",
                        created_at=now,
                        updated_at=now,
                    )
                )
            transport = _Transport()
            assistant = _assistant(sessions, transport, now)
            transcript = TranscribedCapture(inbox_id, "Schedule a meeting tomorrow", 4)

            async def fail_after_mark(session, current_claim, at):
                await SqlAlchemySpeechRequestStore().mark_continued(session, current_claim, at=at)
                raise RuntimeError("crash before final capture transaction commit")

            with pytest.raises(RuntimeError, match="before final capture"):
                await continue_transcribed_capture(
                    sessions,
                    claim,
                    transcript=transcript,
                    assistant=assistant,
                    mark_continued=fail_after_mark,
                    now=now,
                )
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                assert speech.state == "transcribed" and speech.transcript is not None
                assert (await session.get(Job, claim.job_id)).status == "leased"
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.causation_id == claim.job_id)
                    )
                    == 0
                )
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner)
                )
                assert draft.state == "processing"

            async def mark(session, current_claim, at):
                await SqlAlchemySpeechRequestStore().mark_continued(session, current_claim, at=at)

            assert await continue_transcribed_capture(
                sessions,
                claim,
                transcript=transcript,
                assistant=assistant,
                mark_continued=mark,
                now=now,
            )
            assert transport.calls == 1
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                assert speech.state == "continued" and speech.transcript is None
                assert (await session.get(Job, claim.job_id)).status == "succeeded"
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.causation_id == claim.job_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PlanItemRecord)
                        .where(PlanItemRecord.owner_id == owner)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    run_local_async(run)
