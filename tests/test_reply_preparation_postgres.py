from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from processing_consent_fixtures import seed_accepted_processing_consent
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    ReplyPreparation,
    TelegramReplySend,
)
from pin.contexts.notifications.infrastructure.delivery_worker import (
    DELIVERY_REPLY_JOB_KIND,
    deliver_reply_job,
    enqueue_due_reply_jobs,
)
from pin.contexts.notifications.infrastructure.models import DeliveryAttemptRecord
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobClaim, JobLane, LeaseLost, claim_job
from pin.platform.models import Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_REPLY_PREPARATION_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_REPLY_PREPARATION_DATABASE_URL to the issue-19 test database",
)


@pytest.fixture(scope="session")
def reply_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    if DATABASE_URL is None:
        pytest.skip("PIN_TEST_REPLY_PREPARATION_DATABASE_URL is unset")
    url = make_url(DATABASE_URL)
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError("reply preparation tests require postgresql+psycopg")
    if (url.host, url.port, url.username) != ("127.0.0.1", 51017, "pin_test"):
        raise RuntimeError(
            "reply preparation tests refuse a database outside the dedicated local test server"
        )
    if not url.database or not url.database.startswith("pin_issue19_reply_"):
        raise RuntimeError(
            "reply preparation tests refuse databases without the pin_issue19_reply_ prefix"
        )
    sync_engine = create_sync_engine(url)
    try:
        with sync_engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
            if directory.rstrip("/").casefold() != DATA_DIRECTORY.casefold():
                raise RuntimeError(
                    "reply preparation tests refuse an unexpected PostgreSQL data directory"
                )
            if not 180000 <= version < 190000:
                raise RuntimeError("reply preparation tests require PostgreSQL 18")
    finally:
        sync_engine.dispose()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(DATABASE_URL)
    sessions = create_session_factory(engine)
    yield sessions
    run_local_async(engine.dispose)


class _ReplyTransport:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], event_id: UUID
    ) -> None:
        self.sessions = sessions
        self.event_id = event_id
        self.sent: list[TelegramReplySend] = []
        self.failures: list[BaseException] = []
        self.started_was_committed: list[bool] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        return await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.sent.append(message)
        async with self.sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.reply_event_id == self.event_id)
                .order_by(DeliveryAttemptRecord.attempt_number.desc())
            )
            self.started_was_committed.append(attempt is not None and attempt.outcome == "started")
        if self.failures:
            raise self.failures.pop(0)
        return f"telegram-message-{len(self.sent)}"

async def _seed_reply(
    sessions: async_sessionmaker[AsyncSession],
    *,
    preparation_marker: object = True,
    include_marker: bool = True,
    with_access_period: bool = False,
) -> tuple[UUID, UUID, int]:
    owner_id = uuid4()
    event_id = uuid4()
    chat_id = uuid4().int % 2_000_000_000 + 1
    bot_id = uuid4().int % 2_000_000_000 + 1
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(TelegramOwnerRecord(bot_id=bot_id, telegram_user_id=chat_id, owner_id=owner_id))
        authorization = await seed_accepted_processing_consent(
            session,
            owner_id=owner_id,
            bot_id=bot_id,
            telegram_user_id=chat_id,
        )
        if with_access_period:
            session.add(
                AccessPeriodRecord(
                    owner_id=owner_id,
                    period_id="reply-preparation-test-period",
                    policy_kind="trial",
                    policy_version="test",
                    starts_at=now - timedelta(days=1),
                    ends_at=now + timedelta(days=1),
                    ai_turn_limit=10,
                    voice_second_limit=600,
                )
            )
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "markdown": "Queued copy",
            "plain_text": "Queued copy",
            "processing_classification": "ai",
            "processing_grant_id": str(authorization.grant_id),
            "processing_policy_version": authorization.policy_version,
        }
        if include_marker:
            payload["requires_preparation"] = preparation_marker
        await add_outbox_event(
            session,
            EventEnvelope(
                id=event_id,
                event_type="telegram.reply.requested",
                schema_version=1,
                aggregate_id=owner_id,
                aggregate_version=1,
                owner_id=owner_id,
                occurred_at=now,
                payload=payload,
            ),
        )
        await session.flush()
        event = await session.get(OutboxEvent, event_id)
        assert event is not None and event.created_at is not None
        assert await enqueue_due_reply_jobs(session, now=event.created_at) == 1
        job = await session.scalar(select(Job).where(Job.deduplication_key == f"reply:{event_id}"))
        assert job is not None
        job.priority = 2_147_483_647
    return owner_id, event_id, chat_id


async def _claim_reply(
    sessions: async_sessionmaker[AsyncSession], event_id: UUID, *, worker_id: str
) -> JobClaim:
    async with sessions() as session, session.begin():
        claim = await claim_job(
            session,
            lane=JobLane.DELIVERY,
            worker_id=worker_id,
            lease_for=timedelta(minutes=2),
        )
        assert claim is not None and claim.kind == DELIVERY_REPLY_JOB_KIND
        assert claim.payload.get("reply_event_id") == str(event_id)
        return claim


def _transport(sessions: async_sessionmaker[AsyncSession], event_id: UUID) -> _ReplyTransport:
    return _ReplyTransport(sessions, event_id)


def test_prepared_copy_replaces_queued_copy_and_started_attempt_is_committed_before_send(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        owner_id, event_id, chat_id = await _seed_reply(reply_sessions)
        claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-send")
        transport = _transport(reply_sessions, event_id)

        async def prepare(_session, envelope, fallback, _at):
            assert envelope.payload["plain_text"] == "Queued copy"
            assert fallback.plain_text == "Queued copy"
            return ReplyPreparation(
                message=TelegramReplySend(chat_id, "Prepared copy", "Prepared copy")
            )

        outcome = await deliver_reply_job(
            reply_sessions,
            claim,
            transport,
            now=lambda: datetime.now(UTC),
            prepare_reply=prepare,
        )
        assert outcome is not None and outcome.outcome.value == "confirmed"
        assert len(transport.sent) == 1
        assert transport.sent[0].plain_text == "Prepared copy"
        assert transport.started_was_committed == [True]
        async with reply_sessions() as session:
            event = await session.get(OutboxEvent, event_id)
            account = await session.get(AccessAccountRecord, owner_id)
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.reply_event_id == event_id
                )
            )
            assert event is not None and event.payload["payload"]["plain_text"] == "Queued copy"
            assert account is not None
            assert attempt is not None and attempt.outcome == "confirmed"

    run_local_async(scenario)


def test_failed_technical_retry_rechecks_expiry_and_skips_without_a_second_send(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        owner_id, event_id, chat_id = await _seed_reply(reply_sessions, with_access_period=True)
        transport = _transport(reply_sessions, event_id)
        transport.failures.append(
            KnownDeliveryFailure("temporary_provider_error", retry_after=timedelta(milliseconds=20))
        )
        callback_calls = 0

        async def prepare(session, _envelope, _fallback, at):
            nonlocal callback_calls
            callback_calls += 1
            period = await session.get(
                AccessPeriodRecord, (owner_id, "reply-preparation-test-period")
            )
            assert period is not None
            if period.ends_at <= at:
                return ReplyPreparation(reason="access_expired")
            return ReplyPreparation(message=TelegramReplySend(chat_id, "Current", "Current"))

        first_claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-retry-1")
        first = await deliver_reply_job(
            reply_sessions,
            first_claim,
            transport,
            now=lambda: datetime.now(UTC),
            prepare_reply=prepare,
        )
        assert first is not None and first.outcome.value == "failed"
        assert len(transport.sent) == 1 and transport.started_was_committed == [True]
        async with reply_sessions() as session, session.begin():
            event = await session.get(OutboxEvent, event_id)
            period = await session.get(
                AccessPeriodRecord, (owner_id, "reply-preparation-test-period")
            )
            job = await session.scalar(
                select(Job).where(Job.deduplication_key == f"reply:{event_id}")
            )
            assert event is not None and period is not None and job is not None
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.reply_event_id == event_id
                )
            )
            assert attempt is not None and attempt.outcome == "failed"
            assert job.status == "pending" and job.available_at > datetime.now(UTC)
            period.ends_at = datetime.now(UTC) - timedelta(seconds=1)
        await asyncio.sleep(0.05)
        second_claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-retry-2")
        second = await deliver_reply_job(
            reply_sessions,
            second_claim,
            transport,
            now=lambda: datetime.now(UTC),
            prepare_reply=prepare,
        )
        assert second is not None and second.outcome.value == "skipped"
        assert callback_calls == 2
        assert len(transport.sent) == 1
        async with reply_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord)
                        .where(DeliveryAttemptRecord.reply_event_id == event_id)
                        .order_by(DeliveryAttemptRecord.attempt_number)
                    )
                ).all()
            )
            job = await session.scalar(
                select(Job).where(Job.deduplication_key == f"reply:{event_id}")
            )
            assert [item.outcome for item in attempts] == ["failed", "skipped"]
            assert attempts[1].failure_code == "access_expired"
            assert job is not None and job.status == "succeeded"

    run_local_async(scenario)


def test_defer_schedules_job_without_an_attempt_or_transport_call(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        _owner_id, event_id, _chat_id = await _seed_reply(reply_sessions)
        claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-defer")
        transport = _transport(reply_sessions, event_id)
        now = datetime.now(UTC)
        deferred_until = now + timedelta(hours=10)

        async def prepare(_session, _envelope, _fallback, _at):
            return ReplyPreparation(deferred_until=deferred_until, reason="wait_for_access_window")

        outcome = await deliver_reply_job(
            reply_sessions,
            claim,
            transport,
            now=lambda: now,
            prepare_reply=prepare,
        )
        assert outcome is None
        assert transport.sent == []
        async with reply_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.reply_event_id == event_id
                        )
                    )
                ).all()
            )
            job = await session.scalar(
                select(Job).where(Job.deduplication_key == f"reply:{event_id}")
            )
            assert attempts == []
            assert job is not None and job.status == "pending"
            assert job.attempts == 0
            assert (
                deferred_until - timedelta(seconds=1)
                <= job.available_at
                <= deferred_until + timedelta(seconds=3)
            )

    run_local_async(scenario)


@pytest.mark.parametrize(
    "invalid_case", ["missing_guard", "malformed_marker", "foreign_destination"]
)
def test_required_or_invalid_preparation_fails_closed(
    reply_sessions: async_sessionmaker[AsyncSession], invalid_case: str
) -> None:
    async def scenario() -> None:
        marker = 1 if invalid_case == "malformed_marker" else True
        _owner_id, event_id, chat_id = await _seed_reply(
            reply_sessions,
            preparation_marker=marker,
            include_marker=True,
        )
        claim = await _claim_reply(reply_sessions, event_id, worker_id=f"reply-prep-{invalid_case}")
        transport = _transport(reply_sessions, event_id)
        callback_calls = 0

        async def prepare(_session, _envelope, _fallback, _at):
            nonlocal callback_calls
            callback_calls += 1
            if invalid_case == "malformed_marker":
                return ReplyPreparation(message=TelegramReplySend(chat_id, "Prepared", "Prepared"))
            return ReplyPreparation(message=TelegramReplySend(chat_id + 1, "Prepared", "Prepared"))

        result = await deliver_reply_job(
            reply_sessions,
            claim,
            transport,
            now=lambda: datetime.now(UTC),
            prepare_reply=None if invalid_case == "missing_guard" else prepare,
        )
        assert result is not None and result.outcome.value == "skipped"
        assert transport.sent == []
        assert callback_calls == (0 if invalid_case in {"missing_guard", "malformed_marker"} else 1)
        expected_reason = {
            "missing_guard": "reply_preparation_unavailable",
            "malformed_marker": "reply_preparation_invalid",
            "foreign_destination": "reply_preparation_invalid",
        }[invalid_case]
        async with reply_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.reply_event_id == event_id
                )
            )
            assert attempt is not None and attempt.outcome == "skipped"
            assert attempt.failure_code == expected_reason

    run_local_async(scenario)


def test_reclaimed_started_attempt_becomes_unknown_without_preparation_or_resend(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        owner_id, event_id, chat_id = await _seed_reply(reply_sessions)
        first_claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-started-1")
        async with reply_sessions() as session, session.begin():
            session.add(
                DeliveryAttemptRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    reply_event_id=event_id,
                    target_id=chat_id,
                    dispatch_version=1,
                    attempt_number=1,
                    outcome="started",
                    started_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(Job)
                .where(Job.id == first_claim.job_id)
                .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
            )
        reclaimed = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-started-2")
        transport = _transport(reply_sessions, event_id)
        callback_calls = 0

        async def must_not_prepare(_session, _envelope, _fallback, _at):
            nonlocal callback_calls
            callback_calls += 1
            raise AssertionError("a durable started attempt must be reconciled before preparation")

        outcome = await deliver_reply_job(
            reply_sessions,
            reclaimed,
            transport,
            now=lambda: datetime.now(UTC),
            prepare_reply=must_not_prepare,
        )
        assert outcome is not None and outcome.outcome.value == "unknown"
        assert callback_calls == 0
        assert transport.sent == []
        async with reply_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord)
                        .where(DeliveryAttemptRecord.reply_event_id == event_id)
                        .order_by(DeliveryAttemptRecord.attempt_number)
                    )
                ).all()
            )
            assert len(attempts) == 1 and attempts[0].outcome == "unknown"

    run_local_async(scenario)


def test_expired_claim_cannot_reach_preparation_or_transport(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        _owner_id, event_id, _chat_id = await _seed_reply(reply_sessions)
        claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-expired-claim")
        async with reply_sessions() as session, session.begin():
            await session.execute(
                update(Job)
                .where(Job.id == claim.job_id)
                .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
            )
        transport = _transport(reply_sessions, event_id)
        callback_calls = 0

        async def prepare(_session, _envelope, _fallback, _at):
            nonlocal callback_calls
            callback_calls += 1
            return ReplyPreparation(reason="should_not_run")

        with pytest.raises(LeaseLost):
            await deliver_reply_job(
                reply_sessions,
                claim,
                transport,
                now=lambda: datetime.now(UTC),
                prepare_reply=prepare,
            )
        assert callback_calls == 0
        assert transport.sent == []
        async with reply_sessions() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.reply_event_id == event_id
                        )
                    )
                ).all()
            )
            assert attempts == []
        async with reply_sessions() as session, session.begin():
            await session.execute(
                update(Job)
                .where(Job.id == claim.job_id)
                .values(
                    status="dead",
                    finished_at=func.clock_timestamp(),
                    worker_id=None,
                    lease_token=None,
                    lease_until=None,
                )
            )

    run_local_async(scenario)


def test_unmarked_legacy_reply_sends_queued_copy_without_guard(
    reply_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async def scenario() -> None:
        _owner_id, event_id, _chat_id = await _seed_reply(reply_sessions, include_marker=False)
        claim = await _claim_reply(reply_sessions, event_id, worker_id="reply-prep-legacy")
        transport = _transport(reply_sessions, event_id)
        outcome = await deliver_reply_job(
            reply_sessions,
            claim,
            transport,
            now=lambda: datetime.now(UTC),
        )
        assert outcome is not None and outcome.outcome.value == "confirmed"
        assert [message.plain_text for message in transport.sent] == ["Queued copy"]
        assert transport.started_was_committed == [True]

    run_local_async(scenario)
