"""Compose the private Telegram Speech lane with durable capture continuation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.capture import (
    TranscribedCapture,
    continue_transcribed_capture,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, private_actor
from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.speech_download import TelegramFileBot, download_voice_file
from pin.adapters.telegram.speech_ingress import TelegramVoiceAttachment, private_voice_attachment
from pin.bootstrap.speech import load_speech_model
from pin.contexts.billing.api import (
    AccountLifecycle,
    CheckAccess,
    InvalidTransition,
    Operation,
    QuotaExceeded,
    ReservationState,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.conversation.application.service import BoundedPlanningAssistant
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.speech.application.contracts import (
    AudioDecoder,
    DecodedAudio,
    SpeechRecognition,
    SpeechRecognizer,
)
from pin.contexts.speech.domain.audio import UnusableAudio, validate_declared_audio
from pin.contexts.speech.infrastructure.allowance import SqlAlchemySpeechAllowance
from pin.contexts.speech.infrastructure.decoder import PyAVAudioDecoder
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles
from pin.contexts.speech.infrastructure.store import (
    SpeechRequestConflict,
    SqlAlchemySpeechRequestStore,
)
from pin.contexts.speech.infrastructure.whisper import FasterWhisperSmall
from pin.persistence.uow import SqlAlchemyUnitOfWork
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import (
    JobClaim,
    JobLane,
    LeaseLost,
    claim_job,
    complete_job,
    fail_job,
    lock_job_for_effects,
    record_job_effect_once,
    renew_job_lease,
)
from pin.platform.models import InboxUpdate

logger = logging.getLogger("pin.telegram.speech_worker")
LEASE_FOR = timedelta(seconds=45)
LEASE_RENEW_INTERVAL = timedelta(seconds=12)
POLL_SECONDS = 0.5
CRASH_CLEANUP_INTERVAL_SECONDS = 60 * 60


async def claim_speech_job(
    sessions: async_sessionmaker[AsyncSession], *, worker_id: str
) -> JobClaim | None:
    async with sessions() as session, session.begin():
        return await claim_job(
            session,
            lane=JobLane.SPEECH,
            worker_id=worker_id,
            lease_for=LEASE_FOR,
        )


async def process_speech_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    bot: TelegramFileBot,
    assistant: BoundedPlanningAssistant,
    now: datetime,
    files: PrivateAudioFiles | None = None,
    decoder: AudioDecoder | None = None,
    recognizer: SpeechRecognizer | None = None,
) -> None:
    """Recognize one private voice item and continue the original capture job."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("speech worker clock must be timezone-aware")
    if claim.lane is not JobLane.SPEECH or claim.owner_id is None:
        raise ValueError("speech processing requires an owner-bound speech claim")
    instant = now.astimezone(UTC)
    private_files = files or PrivateAudioFiles()
    audio_decoder = decoder or PyAVAudioDecoder()
    speech_recognizer: SpeechRecognizer = recognizer or _LazyWhisperRecognizer()
    allowance = SqlAlchemySpeechAllowance(sessions)
    speech_store = SqlAlchemySpeechRequestStore()
    downloaded_path: Path | None = None
    inbox: InboxUpdate | None = None
    telegram_user_id = 0
    request_key = ""
    continuation_replay: TranscribedCapture | None = None

    async with _maintain_lease(sessions, claim):
        try:
            async with sessions() as session, session.begin():
                lifecycle = await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(
                    claim.owner_id
                )
                if lifecycle is not AccountLifecycle.PRESENT:
                    await complete_job(session, claim)
                    return
                inbound = await _load_private_voice(session, claim)
                if inbound is None:
                    await complete_job(session, claim)
                    return
                inbox, telegram_user_id, attachment = inbound
                request_key = f"telegram-inbox:{inbox.id}"
                prior = await speech_store.get(session, claim)
                if prior is not None:
                    _validate_replay(prior, inbox, request_key, attachment)
                    if prior.state in {"continued", "failed", "rejected"}:
                        await complete_job(session, claim)
                        return
                    if prior.state == "transcribed":
                        continuation_replay = TranscribedCapture(
                            inbox_id=inbox.id,
                            text=prior.transcript or "",
                            voice_seconds=_billable_seconds(prior.decoded_duration_seconds),
                        )
                    elif prior.state == "processing":
                        failure = await _check_open_reservation(
                            session,
                            claim,
                            request_key=request_key,
                            allowance=allowance,
                            at=instant,
                        )
                        if failure is not None:
                            await _reject_initialized(
                                session,
                                claim,
                                inbox=inbox,
                                chat_id=telegram_user_id,
                                reason=failure,
                                speech_store=speech_store,
                                at=instant,
                            )
                            return
                else:
                    invalid_reason = attachment.invalid_reason
                    if invalid_reason is None:
                        try:
                            validate_declared_audio(
                                attachment.declared_size_bytes,
                                attachment.declared_duration_seconds,
                            )
                        except UnusableAudio:
                            invalid_reason = "invalid_audio_metadata"
                    if invalid_reason is not None:
                        await _reject_before_reservation(
                            session,
                            claim,
                            inbox=inbox,
                            chat_id=telegram_user_id,
                            attachment=attachment,
                            request_key=request_key,
                            reason=invalid_reason,
                            speech_store=speech_store,
                            at=instant,
                        )
                        return
                    assert type(attachment.declared_duration_seconds) is int
                    try:
                        await speech_store.initialize(
                            session,
                            claim,
                            inbox_id=inbox.id,
                            request_key=request_key,
                            declared_size_bytes=(
                                attachment.declared_size_bytes
                                if type(attachment.declared_size_bytes) is int
                                else None
                            ),
                            declared_duration_seconds=attachment.declared_duration_seconds,
                            at=instant,
                        )
                        reservation = await allowance.reserve_in(
                            session,
                            owner_id=claim.owner_id,
                            request_id=request_key,
                            voice_seconds=attachment.declared_duration_seconds,
                            at=instant,
                            claim=claim,
                        )
                    except QuotaExceeded:
                        await _reject_initialized(
                            session,
                            claim,
                            inbox=inbox,
                            chat_id=telegram_user_id,
                            reason="voice_allowance_exhausted",
                            speech_store=speech_store,
                            at=instant,
                        )
                        return
                    if reservation is None:
                        await _reject_initialized(
                            session,
                            claim,
                            inbox=inbox,
                            chat_id=telegram_user_id,
                            reason="voice_unavailable",
                            speech_store=speech_store,
                            at=instant,
                        )
                        return
                    await _queue_status(
                        session,
                        claim,
                        chat_id=telegram_user_id,
                        phase="queued",
                        language=await _language(session, claim.owner_id),
                        now=instant,
                    )
            if continuation_replay is not None:
                await _continue_capture(
                    sessions,
                    claim,
                    transcript=continuation_replay,
                    assistant=assistant,
                    speech_store=speech_store,
                    now=instant,
                )
                return
            # No DB transaction stays open across Telegram I/O, decode, or model inference.
            try:
                downloaded = await download_voice_file(
                    bot,
                    file_id=attachment.file_id or "",
                    files=private_files,
                    owner_id=claim.owner_id,
                )
                downloaded_path = downloaded.path
                async with sessions() as session, session.begin():
                    lifecycle = await SqlAlchemyAccessUsageStore(
                        session
                    ).lock_account_lifecycle(claim.owner_id)
                    if lifecycle is not AccountLifecycle.PRESENT:
                        private_files.remove(downloaded.path)
                        downloaded_path = None
                        await complete_job(session, claim)
                        return
                decoded = await audio_decoder.decode(
                    downloaded.path, size_bytes=downloaded.size_bytes
                )
            except UnusableAudio:
                assert inbox is not None
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="unusable_audio",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return
            except Exception as error:
                logger.error(
                    "speech_download_or_decode_failed: job_id=%s error=%s",
                    claim.job_id,
                    type(error).__name__,
                )
                assert inbox is not None
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="recognition_failed",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return
            measured_seconds = decoded.facts.billable_seconds
            try:
                async with SqlAlchemyUnitOfWork(sessions) as uow:
                    await lock_job_for_effects(uow.session, claim)
                    lifecycle = await SqlAlchemyAccessUsageStore(
                        uow.session
                    ).lock_account_lifecycle(claim.owner_id)
                    if lifecycle is not AccountLifecycle.PRESENT:
                        await complete_job(uow.session, claim)
                        return
                    await speech_store.store_facts(
                        uow.session, claim, audio=decoded.facts, at=datetime.now(UTC)
                    )
                async with SqlAlchemyUnitOfWork(sessions) as uow:
                    await allowance.resize_in(
                        uow.session,
                        owner_id=claim.owner_id,
                        request_id=request_key,
                        voice_seconds=measured_seconds,
                        at=datetime.now(UTC),
                        claim=claim,
                    )
                    await _queue_status(
                        uow.session,
                        claim,
                        chat_id=telegram_user_id,
                        phase="processing",
                        language=await _language(uow.session, claim.owner_id),
                        now=datetime.now(UTC),
                    )
            except QuotaExceeded:
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="voice_allowance_exhausted",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return
            except InvalidTransition:
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="voice_reservation_expired",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return

            try:
                recognition = await speech_recognizer.transcribe(decoded)
            except UnusableAudio:
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="unusable_audio",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return
            except Exception as error:
                logger.error(
                    "speech_recognition_failed: job_id=%s error=%s",
                    claim.job_id,
                    type(error).__name__,
                )
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="recognition_failed",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return

            if len(recognition.text.strip()) > 8_000:
                await _finish_audio_failure(
                    sessions,
                    claim,
                    inbox=inbox,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    reason="recognition_failed",
                    allowance=allowance,
                    speech_store=speech_store,
                    now=datetime.now(UTC),
                )
                return

            if not await _store_authorized_transcript(
                sessions,
                claim,
                request_key=request_key,
                recognition=recognition,
                speech_store=speech_store,
                at=datetime.now(UTC),
            ):
                await _finish_deleted_or_unavailable(
                    sessions,
                    claim,
                    chat_id=telegram_user_id,
                    request_key=request_key,
                    speech_store=speech_store,
                    allowance=allowance,
                    now=datetime.now(UTC),
                )
                return

            await _continue_capture(
                sessions,
                claim,
                transcript=TranscribedCapture(
                    inbox_id=inbox.id,
                    text=recognition.text,
                    voice_seconds=recognition.audio.billable_seconds,
                ),
                assistant=assistant,
                speech_store=speech_store,
                now=datetime.now(UTC),
            )
        except UnusableAudio:
            assert inbox is not None
            await _finish_audio_failure(
                sessions,
                claim,
                inbox=inbox,
                chat_id=telegram_user_id,
                request_key=request_key,
                reason="unusable_audio",
                allowance=allowance,
                speech_store=speech_store,
                now=datetime.now(UTC),
            )
        finally:
            if downloaded_path is not None:
                private_files.remove(downloaded_path)


async def _continue_capture(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    transcript: TranscribedCapture,
    assistant: BoundedPlanningAssistant,
    speech_store: SqlAlchemySpeechRequestStore,
    now: datetime,
) -> None:
    applied = await continue_transcribed_capture(
        sessions,
        claim,
        transcript=transcript,
        assistant=assistant,
        mark_continued=_MarkSpeechContinued(speech_store),
        now=now,
    )
    if not applied:
        raise SpeechRequestConflict("validated Telegram voice inbox was not continued")


class _MarkSpeechContinued:
    def __init__(self, store: SqlAlchemySpeechRequestStore) -> None:
        self._store = store

    async def __call__(self, session: AsyncSession, claim: JobClaim, at: datetime) -> None:
        await self._store.mark_continued(session, claim, at=at)


class _LazyWhisperRecognizer:
    """Load one local Whisper model on first eligible inference and reuse it."""

    def __init__(self) -> None:
        self._recognizer: SpeechRecognizer | None = None
        self._load_lock = asyncio.Lock()

    async def transcribe(self, audio: DecodedAudio) -> SpeechRecognition:
        if self._recognizer is None:
            async with self._load_lock:
                if self._recognizer is None:
                    backend = await asyncio.to_thread(load_speech_model, cpu_threads=1)
                    self._recognizer = FasterWhisperSmall(backend)
        return await self._recognizer.transcribe(audio)


async def _load_private_voice(
    session: AsyncSession, claim: JobClaim
) -> tuple[InboxUpdate, int, TelegramVoiceAttachment] | None:
    await lock_job_for_effects(session, claim)
    raw_inbox_id = claim.payload.get("inbox_id")
    try:
        inbox_id = raw_inbox_id if isinstance(raw_inbox_id, UUID) else UUID(str(raw_inbox_id))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("speech job has an invalid inbox_id") from None
    inbox = await session.get(InboxUpdate, inbox_id)
    if inbox is None:
        raise RuntimeError("speech job references a missing inbox update")
    if inbox.owner_id is None or inbox.owner_id != claim.owner_id:
        raise RuntimeError("speech job owner does not match its persisted inbox")
    attachment = private_voice_attachment(inbox.payload)
    if attachment is None:
        return None
    actor = private_actor(inbox.payload)
    if actor is None or actor[1] != "speech_message":
        return None
    telegram_user_id = actor[0]
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        return None
    if (
        await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=inbox.bot_id, telegram_user_id=telegram_user_id
        )
        != inbox.owner_id
    ):
        raise RuntimeError("persisted Telegram identity mapping changed before speech work")
    message = inbox.payload.get("message")
    chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(chat, dict) or chat.get("id") != telegram_user_id:
        raise RuntimeError("persisted voice message no longer satisfies private-chat checks")
    return inbox, telegram_user_id, attachment


def _validate_replay(
    row: object, inbox: InboxUpdate, request_key: str, attachment: TelegramVoiceAttachment
) -> None:
    if (
        getattr(row, "owner_id", None) != inbox.owner_id
        or getattr(row, "inbox_id", None) != inbox.id
        or getattr(row, "request_key", None) != request_key
    ):
        raise SpeechRequestConflict("speech replay changed its owner or inbox")
    if attachment.invalid_reason is None:
        validate_declared_audio(
            attachment.declared_size_bytes,
            attachment.declared_duration_seconds,
        )
        if (
            getattr(row, "declared_duration_seconds", None) != attachment.declared_duration_seconds
            or getattr(row, "declared_size_bytes", None) != attachment.declared_size_bytes
        ):
            raise SpeechRequestConflict("speech replay changed Telegram-declared audio facts")


async def _reject_before_reservation(
    session: AsyncSession,
    claim: JobClaim,
    *,
    inbox: InboxUpdate,
    chat_id: int,
    attachment: TelegramVoiceAttachment,
    request_key: str,
    reason: str,
    speech_store: SqlAlchemySpeechRequestStore,
    at: datetime,
) -> None:
    duration = attachment.declared_duration_seconds
    if type(duration) is int and duration > 0:
        await speech_store.initialize(
            session,
            claim,
            inbox_id=inbox.id,
            request_key=request_key,
            declared_size_bytes=(
                attachment.declared_size_bytes
                if type(attachment.declared_size_bytes) is int
                and attachment.declared_size_bytes > 0
                else None
            ),
            declared_duration_seconds=duration,
            at=at,
        )
        await _reject_initialized(
            session,
            claim,
            inbox=inbox,
            chat_id=chat_id,
            reason=reason,
            speech_store=speech_store,
            at=at,
        )
        return
    await _queue_failure(session, claim, inbox.owner_id, chat_id, reason, at)
    await complete_job(session, claim)


async def _reject_initialized(
    session: AsyncSession,
    claim: JobClaim,
    *,
    inbox: InboxUpdate,
    chat_id: int,
    reason: str,
    speech_store: SqlAlchemySpeechRequestStore,
    at: datetime,
) -> None:
    await speech_store.mark_failure(session, claim, code=reason, at=at, rejected=True)
    await _queue_failure(session, claim, inbox.owner_id, chat_id, reason, at)
    await complete_job(session, claim)


async def _finish_audio_failure(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    inbox: InboxUpdate,
    chat_id: int,
    request_key: str,
    reason: str,
    allowance: SqlAlchemySpeechAllowance,
    speech_store: SqlAlchemySpeechRequestStore,
    now: datetime,
) -> None:
    assert claim.owner_id is not None
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        reservation = await SqlAlchemyAccessUsageStore(uow.session).get_usage_reservation(
            claim.owner_id, request_key
        )
        if reservation is not None and reservation.state is ReservationState.RESERVED:
            if reservation.expires_at <= now:
                await SqlAlchemyAccessUsageStore(uow.session).expire_reservations(
                    at=now, limit=1_000
                )
            else:
                await allowance.refund_in(
                    uow.session,
                    owner_id=claim.owner_id,
                    request_id=request_key,
                    at=now,
                    claim=claim,
                )
        await speech_store.mark_failure(
            uow.session,
            claim,
            code=reason,
            at=now,
            rejected=reason in {"voice_allowance_exhausted", "voice_reservation_expired"},
        )
        await _queue_failure(uow.session, claim, inbox.owner_id, chat_id, reason, now)
        await complete_job(uow.session, claim)


async def _check_open_reservation(
    session: AsyncSession,
    claim: JobClaim,
    *,
    request_key: str,
    allowance: SqlAlchemySpeechAllowance,
    at: datetime,
) -> str | None:
    assert claim.owner_id is not None
    billing = SqlAlchemyAccessUsageStore(session)
    access = await billing.lock_access(
        CheckAccess(claim.owner_id, Operation.VOICE_TRANSCRIPTION, at)
    )
    reservation = await billing.get_usage_reservation(claim.owner_id, request_key)
    if reservation is None or reservation.ai_turns != 1 or reservation.voice_seconds <= 0:
        return "voice_reservation_expired"
    if reservation.state is not ReservationState.RESERVED or reservation.expires_at <= at:
        if reservation.state is ReservationState.RESERVED:
            await billing.expire_reservations(at=at, limit=1_000)
        return "voice_reservation_expired"
    if not access.allowed:
        await allowance.refund_in(
            session,
            owner_id=claim.owner_id,
            request_id=request_key,
            at=at,
            claim=claim,
        )
        return "voice_unavailable"
    return None


async def _store_authorized_transcript(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    request_key: str,
    recognition: SpeechRecognition,
    speech_store: SqlAlchemySpeechRequestStore,
    at: datetime,
) -> bool:
    """Hold lifecycle, reservation and job fences through durable transcript write."""
    assert claim.owner_id is not None
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        await lock_job_for_effects(uow.session, claim)
        billing = SqlAlchemyAccessUsageStore(uow.session)
        lifecycle = await billing.lock_account_lifecycle(claim.owner_id)
        if lifecycle is not AccountLifecycle.PRESENT:
            return False
        access = await billing.lock_access(
            CheckAccess(claim.owner_id, Operation.VOICE_TRANSCRIPTION, at)
        )
        reservation = await billing.get_usage_reservation(claim.owner_id, request_key)
        can_publish = bool(
            access.allowed
            and reservation is not None
            and reservation.state is ReservationState.RESERVED
            and reservation.expires_at > at
            and reservation.ai_turns == 1
            and reservation.voice_seconds == recognition.audio.billable_seconds
        )
        if can_publish:
            await speech_store.store_transcript(
                uow.session, claim, recognition=recognition, at=at
            )
    return can_publish


async def _finish_deleted_or_unavailable(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    *,
    chat_id: int,
    request_key: str,
    speech_store: SqlAlchemySpeechRequestStore,
    allowance: SqlAlchemySpeechAllowance,
    now: datetime,
) -> None:
    """Discard work after authorization loss; deleted accounts receive no reply."""
    assert claim.owner_id is not None
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        await lock_job_for_effects(uow.session, claim)
        billing = SqlAlchemyAccessUsageStore(uow.session)
        lifecycle = await billing.lock_account_lifecycle(claim.owner_id)
        reservation = await billing.get_usage_reservation(claim.owner_id, request_key)
        if reservation is not None and reservation.state is ReservationState.RESERVED:
            if reservation.expires_at > now:
                await allowance.refund_in(
                    uow.session,
                    owner_id=claim.owner_id,
                    request_id=request_key,
                    at=now,
                    claim=claim,
                )
            else:
                await billing.expire_reservations(at=now, limit=1_000)
        await speech_store.mark_failure(
            uow.session,
            claim,
            code="voice_unavailable",
            at=now,
            rejected=True,
        )
        if lifecycle is AccountLifecycle.PRESENT:
            await _queue_failure(
                uow.session,
                claim,
                claim.owner_id,
                chat_id,
                "voice_unavailable",
                now,
            )
        await complete_job(uow.session, claim)


async def _queue_status(
    session: AsyncSession,
    claim: JobClaim,
    *,
    chat_id: int,
    phase: str,
    language: str,
    now: datetime,
) -> None:
    receipt = await record_job_effect_once(
        session,
        claim,
        effect_key=f"speech_status_{phase}",
        result={"status": phase},
    )
    if not receipt.first_application:
        return
    if language == "en":
        text = "Voice message queued." if phase == "queued" else "Transcribing your voice message."
    else:
        text = (
            "Голосовое сообщение в очереди."
            if phase == "queued"
            else "Расшифровываю голосовое сообщение."
        )
    await _queue_reply(session, claim, chat_id=chat_id, text=text, now=now)


async def _queue_failure(
    session: AsyncSession,
    claim: JobClaim,
    owner_id: UUID | None,
    chat_id: int,
    reason: str,
    now: datetime,
) -> None:
    receipt = await record_job_effect_once(
        session,
        claim,
        effect_key="speech_status_failed",
        result={"status": "failed", "reason": reason},
    )
    if not receipt.first_application:
        return
    language = await _language(session, owner_id)
    english = language == "en"
    messages = {
        "invalid_audio_metadata": (
            "That audio file has invalid details. Please send a shorter voice message or text.",
            "У аудиозаписи некорректные данные. Отправьте более короткое голосовое "
            "сообщение или текст.",
        ),
        "voice_allowance_exhausted": (
            "Your remaining voice allowance cannot cover this recording. Try a shorter "
            "clip or send text.",
            "Оставшегося лимита голосовых сообщений не хватает на эту запись. Отправьте "
            "более короткий фрагмент или текст.",
        ),
        "voice_unavailable": (
            "Voice transcription is unavailable right now. Please send your request as text.",
            "Сейчас расшифровка голосовых сообщений недоступна. Отправьте запрос текстом.",
        ),
        "voice_reservation_expired": (
            "This voice request expired before transcription. Please send it again or use text.",
            "Срок обработки голосового сообщения истёк. Отправьте его ещё раз или "
            "напишите текстом.",
        ),
        "recognition_failed": (
            "I couldn't process that recording. Please try a shorter clip or send text.",
            "Не удалось обработать эту запись. Попробуйте более короткий фрагмент или "
            "отправьте текст.",
        ),
        "unusable_audio": (
            "I couldn't hear usable Russian or English speech in that recording. Please "
            "retry or send text.",
            "Не удалось распознать русскую или английскую речь. Попробуйте ещё раз или "
            "отправьте текст.",
        ),
    }
    text = messages.get(reason, messages["unusable_audio"])[0 if english else 1]
    await _queue_reply(session, claim, chat_id=chat_id, text=text, now=now)


async def _queue_reply(
    session: AsyncSession,
    claim: JobClaim,
    *,
    chat_id: int,
    text: str,
    now: datetime,
) -> None:
    if claim.owner_id is None:
        raise ValueError("Speech reply requires a trusted owner")
    await add_outbox_event(
        session,
        EventEnvelope(
            id=uuid4(),
            event_type="telegram.reply.requested",
            schema_version=1,
            aggregate_id=claim.owner_id,
            aggregate_version=max(1, claim.conversation_sequence or 1),
            owner_id=claim.owner_id,
            occurred_at=now.astimezone(UTC),
            causation_id=claim.job_id,
            payload={
                "chat_id": chat_id,
                "markdown": escape_markdown_v2(text),
                "plain_text": text,
                "handling": "handled",
                "source_job_id": str(claim.job_id),
                "page_index": 0,
                "page_count": 1,
            },
        ),
        claim=claim,
    )


async def _language(session: AsyncSession, owner_id: UUID | None) -> str:
    if owner_id is None:
        return "ru"
    preferences = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
    return preferences.language if preferences and preferences.language in {"ru", "en"} else "ru"


@asynccontextmanager
async def _maintain_lease(
    sessions: async_sessionmaker[AsyncSession], claim: JobClaim
) -> AsyncIterator[None]:
    stopped = asyncio.Event()
    lost: list[BaseException] = []

    async def renew() -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), LEASE_RENEW_INTERVAL.total_seconds())
                return
            except TimeoutError:
                pass
            try:
                async with sessions() as session, session.begin():
                    await renew_job_lease(session, claim, lease_for=LEASE_FOR)
            except BaseException as error:
                lost.append(error)
                return

    task = asyncio.create_task(renew(), name=f"speech-lease-{claim.job_id}")
    try:
        yield
        if lost:
            raise LeaseLost(f"speech job {claim.job_id} lost its lease") from lost[0]
    finally:
        stopped.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def run_speech_worker(
    sessions: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    bot: TelegramFileBot,
    assistant: BoundedPlanningAssistant,
    files: PrivateAudioFiles | None = None,
    decoder: AudioDecoder | None = None,
    recognizer: SpeechRecognizer | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    private_files = files or PrivateAudioFiles()
    cached_recognizer: SpeechRecognizer = recognizer or _LazyWhisperRecognizer()
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(private_files.cleanup_crash_leftovers, now=now())
    next_cleanup = loop.time() + CRASH_CLEANUP_INTERVAL_SECONDS
    while True:
        claim = await claim_speech_job(sessions, worker_id=worker_id)
        if claim is None:
            if loop.time() >= next_cleanup:
                await asyncio.to_thread(private_files.cleanup_crash_leftovers, now=now())
                next_cleanup = loop.time() + CRASH_CLEANUP_INTERVAL_SECONDS
            await asyncio.sleep(POLL_SECONDS)
            continue
        try:
            await process_speech_job(
                sessions,
                claim,
                bot=bot,
                assistant=assistant,
                now=now(),
                files=private_files,
                decoder=decoder,
                recognizer=cached_recognizer,
            )
        except Exception as error:
            logger.error(
                "speech_job_failed: job_id=%s error=%s", claim.job_id, type(error).__name__
            )
            try:
                async with sessions() as session, session.begin():
                    await fail_job(session, claim, error=type(error).__name__)
            except Exception:
                logger.warning("speech_job_failure_record_failed: job_id=%s", claim.job_id)
        if loop.time() >= next_cleanup:
            await asyncio.to_thread(private_files.cleanup_crash_leftovers, now=now())
            next_cleanup = loop.time() + CRASH_CLEANUP_INTERVAL_SECONDS


def _billable_seconds(duration: float | None) -> int:
    if duration is None:
        raise SpeechRequestConflict("transcribed request has no decoded duration")
    return ceil(duration)
