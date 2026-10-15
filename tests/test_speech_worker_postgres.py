from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.speech_download import TelegramFile
from pin.adapters.telegram.speech_worker import (
    _LazyWhisperRecognizer,
    claim_speech_job,
    process_speech_job,
)
from pin.contexts.billing.api import (
    Operation,
    ReserveUsage,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.application.contracts import CompletionTransport
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
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.speech.application.contracts import DecodedAudio, SpeechRecognition
from pin.contexts.speech.domain.audio import UnusableAudio
from pin.contexts.speech.infrastructure.decoder import PyAVAudioDecoder
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobClaim, JobLane, LeaseLost, complete_job
from pin.platform.models import ConversationLease, InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue18_voice_worker_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


@pytest.fixture(scope="module")
def speech_worker_database() -> str:
    database_url = os.getenv("PIN_TEST_SPEECH_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_SPEECH_DATABASE_URL to the assigned issue-18 fixture")
    engine = create_sync_engine(database_url)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX):
        pytest.fail("speech worker PostgreSQL database does not match the assigned fixture")
    if port != DATABASE_PORT:
        pytest.fail("speech worker PostgreSQL port does not match the assigned fixture")
    if directory.replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail("speech worker PostgreSQL data directory is not the assigned cluster")
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return database_url


@pytest.fixture(scope="module")
def local_whisper_recognizer() -> _LazyWhisperRecognizer:
    """Match the worker's process-lifetime model reuse in direct job tests."""
    return _LazyWhisperRecognizer()


class _VoiceBot:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.get_file_calls = 0
        self.download_calls = 0

    async def get_file(self, file_id: str) -> TelegramFile:
        self.get_file_calls += 1
        assert file_id == "trusted-telegram-voice-id"
        return _RemoteFile("voice/file.ogg", len(self.audio))

    async def download_file(
        self,
        file_path: str | Path,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,
        chunk_size: int = 65_536,
        seek: bool = True,
    ) -> BinaryIO | None:
        self.download_calls += 1
        assert file_path == "voice/file.ogg"
        assert seek is False
        if isinstance(destination, (str, Path)) or destination is None:
            raise AssertionError("test voice download expects a writable stream")
        destination.write(self.audio)
        return None


@dataclass(slots=True)
class _RemoteFile:
    file_path: str | None
    file_size: int | None


class _Transport:
    def __init__(
        self,
        *,
        language: str = "en",
        reply: str = "What time should I set the reminder?",
    ) -> None:
        self.calls = 0
        self.language = language
        self.reply = reply
        self.last_payload: dict[str, object] | None = None

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        assert self.calls == 1, "voice continuation must not duplicate model requests"
        self.last_payload = _payload
        return {
            "id": f"voice-worker-{uuid4()}",
            "model": ROUTE,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "kind": "reply",
                                "language": self.language,
                                "text": self.reply,
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 81,
                "completion_tokens": 12,
                "total_tokens": 93,
                "cost": "0.000004",
            },
        }


class _ReplySequenceTransport:
    def __init__(self, *results: dict[str, object]) -> None:
        self.results = list(results)
        self.calls = 0
        self.payloads: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.payloads.append(payload)
        assert self.results, "capture submitted an unexpected provider request"
        result = self.results.pop(0)
        return {
            "id": f"voice-capture-{self.calls}-{uuid4()}",
            "model": ROUTE,
            "choices": [{"message": {"content": json.dumps(result)}}],
            "usage": {
                "prompt_tokens": 81,
                "completion_tokens": 12,
                "total_tokens": 93,
                "cost": "0.000004",
            },
        }


class _ProcessSignal(Protocol):
    def set(self) -> None: ...


class _ProcessBarrierTransport:
    def __init__(self, reached: _ProcessSignal) -> None:
        self.reached = reached

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.reached.set()
        await asyncio.Event().wait()
        raise AssertionError("terminated provider barrier unexpectedly resumed")


class _ProcessBarrierBot(_VoiceBot):
    def __init__(self, audio: bytes, reached: _ProcessSignal) -> None:
        super().__init__(audio)
        self.reached = reached

    async def download_file(
        self,
        file_path: str | Path,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,
        chunk_size: int = 65_536,
        seek: bool = True,
    ) -> BinaryIO | None:
        await super().download_file(
            file_path,
            destination=destination,
            timeout=timeout,
            chunk_size=chunk_size,
            seek=seek,
        )
        assert destination is not None and hasattr(destination, "flush")
        destination.flush()
        self.reached.set()
        await asyncio.Event().wait()
        return None


class _FixedRecognizer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: DecodedAudio, *, language: str) -> SpeechRecognition:
        self.calls += 1
        return SpeechRecognition(self.text, "en", audio.facts)


def _terminated_speech_worker(
    database_url: str,
    claim: JobClaim,
    audio: bytes,
    files_directory: str,
    boundary: str,
    reached: _ProcessSignal,
) -> None:
    async def scenario() -> None:
        engine = create_engine(database_url)
        sessions = create_session_factory(engine)
        from pin.adapters.telegram import speech_worker

        original_continue = speech_worker._continue_capture
        if boundary in {"transcribed", "committed"}:

            async def pause_at_continuation(*args: Any, **kwargs: Any) -> None:
                if boundary == "transcribed":
                    reached.set()
                    await asyncio.Event().wait()
                await original_continue(*args, **kwargs)
                if boundary == "committed":
                    reached.set()
                    await asyncio.Event().wait()

            speech_worker._continue_capture = pause_at_continuation
        transport: CompletionTransport = (
            _ProcessBarrierTransport(reached)
            if boundary == "provider_started"
            else _ReplySequenceTransport(
                {"kind": "reply", "language": "en", "text": "I saved that reminder."}
            )
        )
        await process_speech_job(
            sessions,
            claim,
            bot=(
                _ProcessBarrierBot(audio, reached) if boundary == "download" else _VoiceBot(audio)
            ),
            assistant=_assistant(sessions, transport, datetime.now(UTC)),
            now=datetime.now(UTC),
            files=PrivateAudioFiles(Path(files_directory)),
            recognizer=_FixedRecognizer("Take a break tomorrow."),
        )

    run_local_async(scenario)


async def _seed_voice_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    voice_seconds: int,
    add_later_text: bool = True,
    declared_duration_seconds: int = 4,
    language: str = "en",
    ai_turn_limit: int = 20,
    declared_size_bytes: int = 1024,
) -> tuple[UUID, JobClaim, int, UUID, int | None]:
    bot_id = 7_100_000_000 + uuid4().int % 900_000_000
    user_id = 6_100_000_000 + uuid4().int % 900_000_000
    async with sessions() as session, session.begin():
        owner_id = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"speech-worker-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="issue18-test-v1",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(days=7),
                ai_turn_limit=ai_turn_limit,
                voice_second_limit=voice_seconds,
            )
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language=language,
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
                owner_id=owner_id,
                draft_id=uuid4(),
                mode="initial",
                step="complete",
                draft={},
                base_profile_version=1,
                version=1,
            )
        )
        update_id = uuid4().int % 9_000_000_000_000_000
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload={
                "update_id": update_id,
                "message": {
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": user_id, "type": "private"},
                    "date": int(now.timestamp()),
                    "voice": {
                        "file_id": "trusted-telegram-voice-id",
                        "file_unique_id": f"voice-{uuid4().hex}",
                        "duration": declared_duration_seconds,
                        "file_size": declared_size_bytes,
                    },
                },
            },
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        voice_job_id = await session.scalar(
            select(Job.id).where(
                Job.lane == JobLane.SPEECH.value,
                Job.deduplication_key == f"telegram:{bot_id}:{update_id}",
            )
        )
        assert voice_job_id is not None
        await session.execute(
            update(Job).where(Job.id == voice_job_id).values(priority=2_147_483_647)
        )
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        assert inbox is not None
        inbox_id = inbox.id
    claim = await claim_speech_job(sessions, worker_id=f"issue18-voice-{uuid4().hex[:12]}")
    assert claim is not None and claim.lane is JobLane.SPEECH
    assert claim.owner_id == owner_id
    assert claim.conversation_sequence is not None
    if add_later_text:
        async with sessions() as session, session.begin():
            later_update_id = uuid4().int % 9_000_000_000_000_000
            later_receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload={
                    "update_id": later_update_id,
                    "message": {
                        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                        "chat": {"id": user_id, "type": "private"},
                        "date": int(now.timestamp()),
                        "text": "Also remind me next week",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert later_receipt is not None and later_receipt.accepted
    return owner_id, claim, user_id, inbox_id, claim.conversation_sequence


def _assistant(
    sessions: async_sessionmaker[AsyncSession], transport: CompletionTransport, now: datetime
) -> BoundedPlanningAssistant:
    return BoundedPlanningAssistant(
        transport=transport,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: now,
    )


@pytest.mark.parametrize(
    ("language", "expected_transcript", "reply"),
    [
        ("en", "reminder", "What time should I set the reminder?"),
        (
            "ru",
            "\u043d\u0430\u043f\u043e\u043c\u0438\u043d",
            "\u0412\u043e \u0441\u043a\u043e\u043b\u044c\u043a\u043e "
            "\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c "
            "\u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435?",
        ),
    ],
)
def test_voice_worker_runs_actual_local_whisper_and_continues_original_capture(
    speech_worker_database: str,
    tmp_path: Path,
    local_whisper_recognizer: _LazyWhisperRecognizer,
    language: str,
    expected_transcript: str,
    reply: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport(language=language, reply=reply)
        fixture = ROOT / "tests" / "fixtures" / "speech" / f"{language}.wav"
        audio = fixture.read_bytes()
        bot = _VoiceBot(audio)
        try:
            owner_id, claim, user_id, inbox_id, sequence = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, language=language
            )
            recognizer_languages: list[str] = []

            class RecordingRecognizer:
                async def transcribe(
                    self, audio: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    recognizer_languages.append(language)
                    return await local_whisper_recognizer.transcribe(audio, language=language)

            async with sessions() as session:
                queued_text = await session.scalar(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.lane == JobLane.APPLICATION.value,
                        Job.kind == "conversation:message",
                    )
                )
                conversation_lease = await session.get(ConversationLease, owner_id)
                assert queued_text is not None and queued_text.status == "pending"
                assert queued_text.conversation_sequence is not None
                assert sequence is not None and queued_text.conversation_sequence > sequence
                assert conversation_lease is not None
                assert conversation_lease.job_id == claim.job_id
                assert conversation_lease.lease_until > now
            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=PrivateAudioFiles(tmp_path / "private-speech"),
                recognizer=RecordingRecognizer(),
            )
            assert recognizer_languages == [language]
            assert bot.get_file_calls == bot.download_calls == 1
            assert transport.calls == 1
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                capture_input = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
                )
                replies = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                plans = await session.scalar(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
                assert speech is not None and speech.state == "continued"
                assert speech.transcript is None and speech.language is None
                assert speech.actual_size_bytes == len(audio)
                assert speech.decoded_duration_seconds is not None
                assert 3.0 <= speech.decoded_duration_seconds <= 5.0
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "settled"
                assert reservation.settled_ai_turns == 1
                assert reservation.settled_voice_seconds == (4 if language == "en" else 5)
                assert capture_input is not None and capture_input.state == "complete"
                assert capture_input.sequence == 1
                assert len(replies) == 3  # queued, transcribing, capture clarification
                assert any(reply in event.payload["payload"]["plain_text"] for event in replies)
                assert transport.last_payload is not None
                assert expected_transcript.casefold() in str(transport.last_payload).casefold()
                assert plans is None
                assert claim.owner_id == owner_id
                assert claim.conversation_sequence == sequence
                assert job.conversation_sequence == sequence
                assert user_id > 0
                later_text = await session.scalar(
                    select(Job).where(
                        Job.owner_id == owner_id,
                        Job.lane == JobLane.APPLICATION.value,
                        Job.kind == "conversation:message",
                    )
                )
                assert later_text is not None and later_text.status == "pending"
                assert later_text.conversation_sequence is not None
                assert job.conversation_sequence is not None
                assert later_text.conversation_sequence > job.conversation_sequence
                released_lease = await session.get(ConversationLease, owner_id)
                assert released_lease is not None and released_lease.job_id is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_actual_voice_capture_clarification_text_preview_and_confirmation_saves_exact_plan(
    speech_worker_database: str,
    tmp_path: Path,
    local_whisper_recognizer: _LazyWhisperRecognizer,
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        start_date = now.astimezone(ZoneInfo("Asia/Almaty")).date() + timedelta(days=2)
        local_start = f"{start_date.isoformat()}T15:00:00"
        local_end = f"{start_date.isoformat()}T16:00:00"
        transport = _ReplySequenceTransport(
            {
                "kind": "proposal",
                "language": "en",
                "operation": "propose_create_plan",
                "fields": {
                    "title": "Dentist appointment",
                    "schedule": {
                        "kind": "appointment",
                        "local_start": local_start,
                        "timezone": "Asia/Almaty",
                    },
                },
            },
            {
                "kind": "proposal",
                "language": "en",
                "operation": "propose_create_plan",
                "fields": {
                    "title": "Dentist appointment",
                    "schedule": {
                        "kind": "appointment",
                        "local_start": local_start,
                        "local_end": local_end,
                        "timezone": "Asia/Almaty",
                    },
                },
            },
        )
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        files = PrivateAudioFiles(tmp_path / "voice-capture-private")
        try:
            owner_id, voice_claim, user_id, voice_inbox_id, voice_sequence = await _seed_voice_job(
                sessions,
                now=now,
                voice_seconds=900,
                add_later_text=False,
                language="en",
            )
            async with sessions() as session:
                voice_inbox = await session.get(InboxUpdate, voice_inbox_id)
                assert voice_inbox is not None
                bot_id = voice_inbox.bot_id

            await process_speech_job(
                sessions,
                voice_claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=files,
                recognizer=local_whisper_recognizer,
            )
            assert transport.calls == 1
            assert bot.get_file_calls == bot.download_calls == 1
            first_context = json.loads(
                transport.payloads[0]["messages"][1]["content"]  # type: ignore[index]
            )
            assert first_context["input_kind"] == "transcript"
            assert "reminder" in first_context["text"].casefold()

            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, voice_claim.job_id)
                voice_reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{voice_inbox_id}",
                    )
                )
                previewless_plans = await session.scalar(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
                assert speech is not None and speech.state == "continued"
                assert speech.transcript is None and speech.language is None
                assert voice_reservation is not None and voice_reservation.state == "settled"
                assert voice_reservation.settled_ai_turns == 1
                assert voice_reservation.settled_voice_seconds == 4
                assert previewless_plans is None

            # A native private text update supplies the missing appointment end.
            text_update_id = uuid4().int % 9_000_000_000_000_000
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload={
                        "update_id": text_update_id,
                        "message": {
                            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                            "chat": {"id": user_id, "type": "private"},
                            "date": int(now.timestamp()),
                            "text": "It ends at 16:00, one hour later.",
                        },
                    },
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
                text_inbox = await session.scalar(
                    select(InboxUpdate).where(
                        InboxUpdate.bot_id == bot_id,
                        InboxUpdate.update_id == text_update_id,
                    )
                )
                assert text_inbox is not None
                text_inbox_id = text_inbox.id
                text_job = await session.scalar(
                    select(Job).where(
                        Job.deduplication_key == f"telegram:{bot_id}:{text_update_id}"
                    )
                )
                assert text_job is not None and text_job.lane == JobLane.APPLICATION.value
                text_job.priority = 2_147_483_647

            text_claim = await claim_application_job(
                sessions, worker_id=f"voice-capture-text-{uuid4().hex[:10]}"
            )
            assert text_claim is not None and text_claim.owner_id == owner_id
            assert text_claim.conversation_sequence is not None
            assert voice_sequence is not None and text_claim.conversation_sequence > voice_sequence
            await process_application_job(
                sessions,
                text_claim,
                now=now,
                assistant=_assistant(sessions, transport, now),
            )
            assert transport.calls == 2
            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                text_reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{text_inbox_id}",
                    )
                )
                no_plan_before_confirmation = await session.scalar(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
                preview_event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "telegram.reply.requested",
                        OutboxEvent.causation_id == text_claim.job_id,
                    )
                )
                assert draft is not None and draft.state == "ready"
                assert draft.preview is not None
                assert text_reservation is not None and text_reservation.state == "settled"
                assert text_reservation.settled_ai_turns == 1
                assert text_reservation.settled_voice_seconds == 0
                assert no_plan_before_confirmation is None
                assert preview_event is not None
                preview_payload = preview_event.payload["payload"]
                assert "Dentist appointment" in preview_payload["plain_text"]
                markup = preview_payload["reply_markup"]
                confirm_tokens = [
                    button["callback_data"]
                    for row in markup["inline_keyboard"]
                    for button in row
                    if str(button.get("callback_data", "")).startswith("capc:")
                ]
                assert len(confirm_tokens) == 1
                confirm_data = str(confirm_tokens[0])
                assert "capx:" in str(markup)

            # Persist the exact confirmation callback rendered in the durable preview outbox.
            callback_update_id = uuid4().int % 9_000_000_000_000_000
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload={
                        "update_id": callback_update_id,
                        "callback_query": {
                            "id": f"voice-confirm-{uuid4().hex}",
                            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                            "message": {
                                "message_id": 301,
                                "chat": {"id": user_id, "type": "private"},
                            },
                            "data": confirm_data,
                        },
                    },
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
                callback_job = await session.scalar(
                    select(Job).where(
                        Job.deduplication_key == f"telegram:{bot_id}:{callback_update_id}"
                    )
                )
                assert callback_job is not None
                callback_job.priority = 2_147_483_647

            callback_claim = await claim_application_job(
                sessions, worker_id=f"voice-capture-confirm-{uuid4().hex[:10]}"
            )
            assert callback_claim is not None and callback_claim.owner_id == owner_id
            await process_application_job(sessions, callback_claim, now=now, assistant=None)
            assert transport.calls == 2  # confirmation performs no provider request

            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                plans = list(
                    (
                        await session.scalars(
                            select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                        )
                    ).all()
                )
                occurrences = list(
                    (
                        await session.scalars(
                            select(OccurrenceRecord).where(OccurrenceRecord.owner_id == owner_id)
                        )
                    ).all()
                )
                reservations = list(
                    (
                        await session.scalars(
                            select(UsageReservationRecord).where(
                                UsageReservationRecord.owner_id == owner_id
                            )
                        )
                    ).all()
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
                assert draft is not None and draft.state == "confirmed"
                assert len(plans) == len(occurrences) == 1
                assert plans[0].kind == "appointment"
                assert plans[0].title == "Dentist appointment"
                assert occurrences[0].local_date == start_date
                assert occurrences[0].local_time == time(15)
                assert occurrences[0].end_local_date == start_date
                assert occurrences[0].end_local_time == time(16)
                assert occurrences[0].duration_seconds == 3_600
                reminders = list(
                    (
                        await session.scalars(
                            select(ReminderRuleRecord).where(
                                ReminderRuleRecord.owner_id == owner_id,
                                ReminderRuleRecord.occurrence_id == occurrences[0].id,
                            )
                        )
                    ).all()
                )
                assert {(item.offset_kind, item.offset_amount) for item in reminders} == {
                    ("calendar_days", 1),
                    ("elapsed_seconds", 7_200),
                }
                assert len(reservations) == 2
                assert {
                    (
                        item.reserved_ai_turns,
                        item.reserved_voice_seconds,
                        item.settled_ai_turns,
                        item.settled_voice_seconds,
                        item.state,
                    )
                    for item in reservations
                } == {
                    (1, 4, 1, 4, "settled"),
                    (1, 0, 1, 0, "settled"),
                }
                assert len(calls) == 2 and all(item.state == "reported" for item in calls)
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_voice_allowance_rejects_before_telegram_download(
    speech_worker_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        bot = _VoiceBot((ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes())
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=3, add_later_text=False
            )

            from pin.adapters.telegram import speech_worker

            def unexpected_model_load(**_kwargs: object) -> object:
                raise AssertionError("local model loaded despite insufficient voice quota")

            monkeypatch.setattr(speech_worker, "load_speech_model", unexpected_model_load)

            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=PrivateAudioFiles(tmp_path / "denied-speech"),
            )
            assert bot.get_file_calls == bot.download_calls == 0
            assert transport.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                failure = await session.scalar(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == "telegram.reply.requested",
                        OutboxEvent.causation_id == claim.job_id,
                    )
                    .order_by(OutboxEvent.created_at.desc())
                )
                assert speech is not None and speech.state == "rejected"
                assert speech.failure_code == "voice_allowance_exhausted"
                assert job is not None and job.status == "succeeded"
                assert reservation is None
                assert failure is not None
                assert "shorter" in failure.payload["payload"]["plain_text"].lower()
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_failed_local_recognition_refunds_hold_and_keeps_provider_unused(
    speech_worker_database: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        files = PrivateAudioFiles(tmp_path / "failed-private-speech")
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )

            class NoSpeechRecognizer:
                async def transcribe(
                    self, _audio: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    raise UnusableAudio("no supported speech was recognized")

            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=files,
                recognizer=NoSpeechRecognizer(),
            )
            assert transport.calls == 0
            assert bot.get_file_calls == bot.download_calls == 1
            assert list(files.directory.iterdir()) == []
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "failed"
                assert speech.failure_code == "unusable_audio"
                assert speech.transcript is None and speech.language is None
                assert speech.actual_size_bytes == len(audio)
                assert speech.decoded_duration_seconds is not None
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "refunded"
                assert reservation.settled_ai_turns == reservation.settled_voice_seconds == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_missing_profile_language_fails_closed_before_recognition(
    speech_worker_database: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        recognizer = _FixedRecognizer("Take a break tomorrow.")
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False, language="en"
            )
            async with sessions() as session, session.begin():
                preferences = await session.get(PreferencesRecord, owner_id)
                assert preferences is not None
                await session.delete(preferences)

            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, _Transport(), now),
                now=now,
                files=PrivateAudioFiles(tmp_path / "missing-language-private"),
                recognizer=recognizer,
            )

            assert recognizer.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.failure_code == "speech_language_unavailable"
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "refunded"
                assert reservation.settled_ai_turns == reservation.settled_voice_seconds == 0
            assert list((tmp_path / "missing-language-private").glob("audio-*.bin")) == []
        finally:
            await engine.dispose()

    run_local_async(scenario)


async def _expire_and_reclaim(
    sessions: async_sessionmaker[AsyncSession], claim: JobClaim
) -> JobClaim:
    expired = datetime.now(UTC) - timedelta(seconds=1)
    async with sessions() as session, session.begin():
        await session.execute(update(Job).where(Job.id == claim.job_id).values(lease_until=expired))
        assert claim.owner_id is not None
        await session.execute(
            update(ConversationLease)
            .where(ConversationLease.owner_id == claim.owner_id)
            .values(lease_until=expired)
        )
    recovered = await claim_speech_job(sessions, worker_id=f"reclaimed-{uuid4().hex[:8]}")
    assert recovered is not None and recovered.job_id == claim.job_id
    assert recovered.token != claim.token
    return recovered


@pytest.mark.parametrize("pause_at", ["decode", "inference"])
def test_expired_reclaimed_worker_cannot_publish_after_audio_stage(
    speech_worker_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pause_at: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        files = PrivateAudioFiles(tmp_path / f"reclaimed-{pause_at}")
        entered = asyncio.Event()
        release = asyncio.Event()
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )
            from pin.adapters.telegram import speech_worker

            monkeypatch.setattr(speech_worker, "LEASE_RENEW_INTERVAL", timedelta(hours=1))
            real_decoder = PyAVAudioDecoder()

            class PausedDecoder:
                async def decode(self, path: Path, *, size_bytes: int) -> DecodedAudio:
                    result = await real_decoder.decode(path, size_bytes=size_bytes)
                    if pause_at == "decode":
                        entered.set()
                        await release.wait()
                    return result

            class PausedRecognizer:
                async def transcribe(
                    self, decoded: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    if pause_at == "inference":
                        entered.set()
                        await release.wait()
                    return SpeechRecognition("Add a reminder for tomorrow.", "en", decoded.facts)

            worker = asyncio.create_task(
                process_speech_job(
                    sessions,
                    claim,
                    bot=bot,
                    assistant=_assistant(sessions, transport, now),
                    now=now,
                    files=files,
                    decoder=PausedDecoder(),
                    recognizer=PausedRecognizer(),
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=20)
            recovered = await _expire_and_reclaim(sessions, claim)
            release.set()
            with pytest.raises(LeaseLost):
                await worker
            assert list(files.directory.iterdir()) == []
            assert transport.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                capture_input = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
                )
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "processing"
                assert speech.transcript is None and speech.language is None
                assert capture_input is None
                assert reservation is not None and reservation.state == "reserved"
                replies = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                assert len(replies) == (1 if pause_at == "decode" else 2)
            async with sessions() as session, session.begin():
                await complete_job(session, recovered)
        finally:
            if not release.is_set():
                release.set()
            await engine.dispose()

    run_local_async(scenario)


def test_deleted_account_during_inference_discards_transcript_and_refunds(
    speech_worker_database: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        entered = asyncio.Event()
        release = asyncio.Event()
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )

            class PausedRecognizer:
                async def transcribe(
                    self, decoded: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    entered.set()
                    await release.wait()
                    return SpeechRecognition("Add a reminder tomorrow.", "en", decoded.facts)

            worker = asyncio.create_task(
                process_speech_job(
                    sessions,
                    claim,
                    bot=bot,
                    assistant=_assistant(sessions, transport, now),
                    now=now,
                    files=PrivateAudioFiles(tmp_path / "deleted-owner"),
                    recognizer=PausedRecognizer(),
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=20)
            async with sessions() as session, session.begin():
                account = await session.get(AccessAccountRecord, owner_id, with_for_update=True)
                assert account is not None
                account.deleted_at = datetime.now(UTC)
            release.set()
            await worker
            assert transport.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                capture = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
                )
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "rejected"
                assert speech.transcript is None and speech.language is None
                assert speech.actual_size_bytes == len(audio)
                assert capture is None
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "refunded"
                replies = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                assert len(replies) == 2  # queued and processing only
        finally:
            release.set()
            await engine.dispose()

    run_local_async(scenario)


def test_durable_transcript_recovers_without_second_telegram_download(
    speech_worker_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )
            from pin.adapters.telegram import speech_worker

            async def crash_before_continuation(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError("simulated crash after transcript commit")

            monkeypatch.setattr(speech_worker, "_continue_capture", crash_before_continuation)

            class FixedRecognizer:
                async def transcribe(
                    self, decoded: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    return SpeechRecognition("Add a reminder for tomorrow.", "en", decoded.facts)

            with pytest.raises(RuntimeError, match="simulated crash"):
                await process_speech_job(
                    sessions,
                    claim,
                    bot=bot,
                    assistant=_assistant(sessions, transport, now),
                    now=now,
                    files=PrivateAudioFiles(tmp_path / "crash-recovery"),
                    recognizer=FixedRecognizer(),
                )
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                assert speech is not None and speech.state == "transcribed"
                assert speech.transcript is not None
            recovered = await _expire_and_reclaim(sessions, claim)
            monkeypatch.undo()

            class UnexpectedRecognizer:
                async def transcribe(
                    self, _decoded: DecodedAudio, *, language: str
                ) -> SpeechRecognition:
                    raise AssertionError("recovery must reuse the durable transcript")

            await process_speech_job(
                sessions,
                recovered,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=datetime.now(UTC),
                files=PrivateAudioFiles(tmp_path / "crash-recovery"),
                recognizer=UnexpectedRecognizer(),
            )
            assert bot.get_file_calls == bot.download_calls == 1
            assert transport.calls == 1
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                capture = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "continued"
                assert speech.transcript is None and speech.language is None
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "settled"
                assert reservation.settled_ai_turns == 1
                assert reservation.settled_voice_seconds == 4
                assert capture is not None and capture.state == "complete"
                replies = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                assert len(replies) == 3
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_understated_duration_crossing_quota_keeps_facts_and_skips_model(
    speech_worker_database: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        bot = _VoiceBot(audio)
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions,
                now=now,
                voice_seconds=3,
                add_later_text=False,
                declared_duration_seconds=1,
                declared_size_bytes=1,
            )
            from pin.adapters.telegram import speech_worker

            def unexpected_model_load(**_kwargs: object) -> object:
                raise AssertionError("understated clip crossed quota but loaded the model")

            monkeypatch.setattr(speech_worker, "load_speech_model", unexpected_model_load)
            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=PrivateAudioFiles(tmp_path / "understated-duration"),
            )
            assert bot.get_file_calls == bot.download_calls == 1
            assert transport.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "rejected"
                assert speech.failure_code == "voice_allowance_exhausted"
                assert speech.declared_size_bytes == 1
                assert speech.actual_size_bytes == len(audio)
                assert speech.decoded_duration_seconds is not None
                assert speech.decoded_duration_seconds > 1
                assert reservation is not None and reservation.state == "refunded"
                assert reservation.settled_ai_turns == reservation.settled_voice_seconds == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_exhausted_ai_allowance_stops_before_telegram_download(
    speech_worker_database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        transport = _Transport()
        bot = _VoiceBot((ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes())
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions,
                now=now,
                voice_seconds=900,
                add_later_text=False,
                ai_turn_limit=1,
            )
            async with sessions() as session, session.begin():
                billing = SqlAlchemyAccessUsageStore(session)
                usage_key = f"consume-ai:{uuid4()}"
                result = await billing.reserve_usage(
                    ReserveUsage(
                        owner_id=owner_id,
                        request_id=usage_key,
                        operation=Operation.AI_TURN,
                        ai_turns=1,
                        voice_seconds=0,
                        at=now,
                        expires_at=now + timedelta(minutes=1),
                    )
                )
                assert result.reservation is not None
                await billing.settle_usage(
                    owner_id=owner_id,
                    request_id=usage_key,
                    ai_turns=1,
                    voice_seconds=0,
                    at=now,
                )
            from pin.adapters.telegram import speech_worker

            def unexpected_model_load(**_kwargs: object) -> object:
                raise AssertionError("model loaded after AI allowance was exhausted")

            monkeypatch.setattr(speech_worker, "load_speech_model", unexpected_model_load)
            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=_assistant(sessions, transport, now),
                now=now,
                files=PrivateAudioFiles(tmp_path / "ai-exhausted"),
            )
            assert bot.get_file_calls == bot.download_calls == 0
            assert transport.calls == 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                assert speech is not None and speech.state == "rejected"
                assert speech.failure_code == "voice_allowance_exhausted"
                assert reservation is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize("boundary", ["download", "transcribed", "provider_started"])
def test_spawned_worker_death_reclaims_original_voice_job_without_duplicate_work(
    speech_worker_database: str,
    tmp_path: Path,
    boundary: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        directory = tmp_path / f"process-death-{boundary}"
        context = mp.get_context("spawn")
        reached = context.Event()
        process: Any = None
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(Job).where(Job.id == claim.job_id).values(priority=2_147_483_647)
                )

            process = context.Process(
                target=_terminated_speech_worker,
                args=(
                    speech_worker_database,
                    claim,
                    audio,
                    str(directory),
                    boundary,
                    reached,
                ),
            )
            process.start()
            arrived = await asyncio.wait_for(asyncio.to_thread(reached.wait, 60), timeout=65)
            assert arrived, f"spawned worker did not reach {boundary}; exit={process.exitcode}"
            process.terminate()
            await asyncio.to_thread(process.join, 20)
            assert process.exitcode is not None and process.exitcode != 0

            leftovers = list(directory.glob("audio-*.bin"))
            assert len(leftovers) == 1
            assert leftovers[0].stat().st_size == len(audio)
            async with sessions() as session:
                crashed_speech = await session.get(SpeechRequestRecord, claim.job_id)
                crashed_reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                crashed_calls = list(
                    (
                        await session.scalars(
                            select(ProviderCallRecord).where(
                                ProviderCallRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                assert crashed_speech is not None
                assert crashed_reservation is not None
                if boundary == "download":
                    assert crashed_speech.state == "processing"
                    assert crashed_speech.actual_size_bytes is None
                    assert crashed_reservation.state == "reserved"
                    assert crashed_calls == []
                else:
                    assert crashed_speech.state == "transcribed"
                    assert crashed_speech.transcript == "Take a break tomorrow."
                    assert crashed_speech.actual_size_bytes == len(audio)
                if boundary == "provider_started":
                    assert len(crashed_calls) == 1
                    assert crashed_calls[0].state == "started"

            if boundary == "provider_started":
                # The provider call's start receipt fences replay from making a second call.
                retry_transport = _ReplySequenceTransport(
                    {"kind": "reply", "language": "en", "text": "Must not be called."}
                )
            else:
                retry_transport = _ReplySequenceTransport(
                    {"kind": "reply", "language": "en", "text": "I saved that reminder."}
                )
            recovered = await _expire_and_reclaim(sessions, claim)
            assert recovered.job_id == claim.job_id
            recognizer = _FixedRecognizer("Take a break tomorrow.")
            retry_bot = _VoiceBot(audio)
            await process_speech_job(
                sessions,
                recovered,
                bot=retry_bot,
                assistant=_assistant(sessions, retry_transport, now),
                now=now,
                files=PrivateAudioFiles(directory),
                recognizer=recognizer,
            )
            if boundary == "download":
                assert retry_bot.get_file_calls == retry_bot.download_calls == 1
                assert recognizer.calls == 1
            else:
                assert retry_bot.get_file_calls == retry_bot.download_calls == 0
                assert recognizer.calls == 0
            assert retry_transport.calls == (0 if boundary == "provider_started" else 1)

            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                capture_input = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
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
                reply_events = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                assert speech is not None and speech.state == "continued"
                assert speech.transcript is None and speech.language is None
                assert job is not None and job.status == "succeeded"
                assert reservation is not None
                assert capture_input is not None
                assert len(calls) == (1 if boundary == "provider_started" else 1)
                assert len(reply_events) == 3
                if boundary == "provider_started":
                    assert calls[0].state == "started"
                    assert capture_input.state == "failed"
                    assert reservation.state == "refunded"
                    assert reservation.settled_ai_turns == reservation.settled_voice_seconds == 0
                else:
                    assert calls[0].state == "reported"
                    assert capture_input.state == "complete"
                    assert reservation.state == "settled"
                    assert reservation.settled_ai_turns == 1
                    assert reservation.settled_voice_seconds == 4

            if boundary == "provider_started":
                with pytest.raises(LeaseLost):
                    await SqlAlchemyTurnAllowance(sessions, voice_seconds=4).refund(
                        owner_id=owner_id,
                        request_id=f"telegram-inbox:{inbox_id}",
                        at=now,
                        claim=claim,
                    )
                async with sessions() as session:
                    reservation = await session.scalar(
                        select(UsageReservationRecord).where(
                            UsageReservationRecord.owner_id == owner_id,
                            UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                        )
                    )
                    assert reservation is not None and reservation.state == "refunded"

            # The crashed process leaves one private file; explicit-clock janitor cleanup
            # removes only generated files once they pass the 24-hour crash bound.
            current = datetime.now(UTC)
            assert PrivateAudioFiles(directory).cleanup_crash_leftovers(now=current) == 0
            stale_epoch = current.timestamp() - timedelta(hours=24, seconds=1).total_seconds()
            os.utime(leftovers[0], (stale_epoch, stale_epoch))
            assert PrivateAudioFiles(directory).cleanup_crash_leftovers(now=current) == 1
            assert list(directory.glob("audio-*.bin")) == []
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 20)
            await engine.dispose()

    run_local_async(scenario)


def test_spawned_worker_death_after_final_continuation_commit_keeps_one_result(
    speech_worker_database: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_engine(speech_worker_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        audio = (ROOT / "tests" / "fixtures" / "speech" / "en.wav").read_bytes()
        directory = tmp_path / "process-death-after-commit"
        context = mp.get_context("spawn")
        reached = context.Event()
        process: Any = None
        try:
            owner_id, claim, _, inbox_id, _ = await _seed_voice_job(
                sessions, now=now, voice_seconds=900, add_later_text=False
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(Job).where(Job.id == claim.job_id).values(priority=2_147_483_647)
                )
            process = context.Process(
                target=_terminated_speech_worker,
                args=(
                    speech_worker_database,
                    claim,
                    audio,
                    str(directory),
                    "committed",
                    reached,
                ),
            )
            process.start()
            arrived = await asyncio.wait_for(asyncio.to_thread(reached.wait, 60), timeout=65)
            assert arrived, f"spawned worker did not commit; exit={process.exitcode}"
            process.terminate()
            await asyncio.to_thread(process.join, 20)
            assert process.exitcode is not None and process.exitcode != 0
            async with sessions() as session:
                speech = await session.get(SpeechRequestRecord, claim.job_id)
                job = await session.get(Job, claim.job_id)
                reservation = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == f"telegram-inbox:{inbox_id}",
                    )
                )
                capture_input = await session.scalar(
                    select(CaptureInputRecord).where(
                        CaptureInputRecord.owner_id == owner_id,
                        CaptureInputRecord.input_key == f"telegram-inbox:{inbox_id}",
                    )
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
                reply_events = (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
                assert speech is not None and speech.state == "continued"
                assert job is not None and job.status == "succeeded"
                assert reservation is not None and reservation.state == "settled"
                assert reservation.settled_ai_turns == 1
                assert reservation.settled_voice_seconds == 4
                assert capture_input is not None and capture_input.state == "complete"
                assert len(calls) == 1 and calls[0].state == "reported"
                assert len(reply_events) == 3
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 20)
            await engine.dispose()

    run_local_async(scenario)
