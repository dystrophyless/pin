from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import Update
from aiohttp.test_utils import TestClient, TestServer
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.ingress import (
    TelegramIngressBotRecord,
    TelegramOwnerRecord,
    select_ingress_mode,
)
from pin.adapters.telegram.polling import poll_updates
from pin.adapters.telegram.webhook import MAX_WEBHOOK_BODY_BYTES, SECRET_HEADER, create_webhook_app
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.jobs import JobLane, claim_job
from pin.platform.models import InboxUpdate, Job

ROOT = Path(__file__).resolve().parents[1]
BOT_TOKEN = "712345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
WEBHOOK_SECRET = "test-webhook-secret-that-is-long-enough"


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def ingress_database() -> Iterator[str]:
    database_url = os.getenv("PIN_TEST_INGRESS_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_INGRESS_DATABASE_URL to the dedicated ingress test database")
    command.upgrade(_alembic_config(database_url), "head")
    yield database_url


def _message(update_id: int, user_id: int, **extra: object) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1,
            "date": 1_760_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": "hello",
            **extra,
        },
    }


def _bot_id() -> int:
    return 800_000_000 + (uuid4().int % 100_000_000)


def test_webhook_authentication_dedup_retry_and_bot_scoped_identity(
    ingress_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, second_bot_id, user_id, update_id = _bot_id(), _bot_id(), 420_001, 9_000_001
        bot, second_bot = Bot(BOT_TOKEN), Bot("712345679:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
        first = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=bot,
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                )
            )
        )
        try:
            await first.start_server()
            denied = await first.post("/telegram/webhook", json=_message(update_id, user_id))
            assert denied.status == 401
            malformed = await first.post(
                "/telegram/webhook",
                data="{bad json",
                headers={SECRET_HEADER: WEBHOOK_SECRET, "Content-Type": "application/json"},
            )
            assert malformed.status == 400
            oversized = await first.post(
                "/telegram/webhook",
                data=b" " * (MAX_WEBHOOK_BODY_BYTES + 1),
                headers={SECRET_HEADER: WEBHOOK_SECRET, "Content-Type": "application/json"},
            )
            assert oversized.status == 413

            body = _message(update_id, user_id, owner_id=str(uuid4()))
            accepted, duplicate = await asyncio.gather(
                first.post("/telegram/webhook", json=body, headers={SECRET_HEADER: WEBHOOK_SECRET}),
                first.post("/telegram/webhook", json=body, headers={SECRET_HEADER: WEBHOOK_SECRET}),
            )
            assert accepted.status == duplicate.status == 200

            second = TestClient(
                TestServer(
                    create_webhook_app(
                        engine=engine,
                        sessions=sessions,
                        bot=second_bot,
                        bot_id=second_bot_id,
                        webhook_secret=WEBHOOK_SECRET,
                    )
                )
            )
            await second.start_server()
            try:
                independent = await second.post(
                    "/telegram/webhook", json=body, headers={SECRET_HEADER: WEBHOOK_SECRET}
                )
                assert independent.status == 200
            finally:
                await second.close()

            async with sessions() as session:
                inboxes = list(
                    (
                        await session.scalars(
                            select(InboxUpdate).where(
                                InboxUpdate.update_id == update_id,
                                InboxUpdate.bot_id.in_([bot_id, second_bot_id]),
                            )
                        )
                    ).all()
                )
                assert len(inboxes) == 2
                assert {row.bot_id for row in inboxes} == {bot_id, second_bot_id}
                assert all("owner_id" not in row.payload["message"] for row in inboxes)
                owners = list(
                    (
                        await session.scalars(
                            select(TelegramOwnerRecord).where(
                                TelegramOwnerRecord.telegram_user_id == user_id,
                                TelegramOwnerRecord.bot_id.in_([bot_id, second_bot_id]),
                            )
                        )
                    ).all()
                )
                assert len(owners) == 2 and owners[0].owner_id != owners[1].owner_id
                jobs = list(
                    (
                        await session.scalars(
                            select(Job).where(
                                Job.deduplication_key.in_(
                                    [
                                        f"telegram:{bot_id}:{update_id}",
                                        f"telegram:{second_bot_id}:{update_id}",
                                    ]
                                )
                            )
                        )
                    ).all()
                )
                assert len(jobs) == 2
                assert all(job.conversation_sequence == 1 for job in jobs)
        finally:
            await first.close()
            # The first app disposes the shared engine as part of cleanup.
            await second_bot.session.close()

    run_local_async(scenario)


def test_webhook_database_failure_returns_retryable_status_and_rolls_back_receipt(
    ingress_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id, update_id = _bot_id(), 420_002, 9_000_002
        suffix = uuid4().hex[:12]
        function_name, trigger_name = f"reject_ingress_{suffix}", f"reject_ingress_{suffix}"
        bot = Bot(BOT_TOKEN)
        client = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=bot,
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                )
            )
        )
        try:
            await client.start_server()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION pin_telegram.{function_name}() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN "
                        f"IF NEW.bot_id = {bot_id} THEN "
                        "RAISE EXCEPTION 'test persistence failure'; "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON platform.inbox "
                        f"FOR EACH ROW EXECUTE FUNCTION pin_telegram.{function_name}()"
                    )
                )
            response = await client.post(
                "/telegram/webhook",
                json=_message(update_id, user_id),
                headers={SECRET_HEADER: WEBHOOK_SECRET},
            )
            assert response.status == 503
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM platform.inbox "
                            "WHERE bot_id=:bot AND update_id=:update"
                        ),
                        {"bot": bot_id, "update": update_id},
                    )
                    == 0
                )
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM pin_telegram.owners "
                            "WHERE bot_id=:bot AND telegram_user_id=:user"
                        ),
                        {"bot": bot_id, "user": user_id},
                    )
                    == 0
                )
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM platform.jobs WHERE deduplication_key=:dedupe"),
                        {"dedupe": f"telegram:{bot_id}:{update_id}"},
                    )
                    == 0
                )
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP TRIGGER {trigger_name} ON platform.inbox"))
                await connection.execute(text(f"DROP FUNCTION pin_telegram.{function_name}()"))
            retry = await client.post(
                "/telegram/webhook",
                json=_message(update_id, user_id),
                headers={SECRET_HEADER: WEBHOOK_SECRET},
            )
            assert retry.status == 200
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(InboxUpdate)
                    .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                )
                assert count == 1
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"DROP TRIGGER IF EXISTS {trigger_name} ON platform.inbox")
                )
                await connection.execute(
                    text(f"DROP FUNCTION IF EXISTS pin_telegram.{function_name}()")
                )
            await client.close()

    run_local_async(scenario)


def test_groups_and_forged_callback_chats_never_create_owner_or_receipt(
    ingress_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id = _bot_id(), 420_003
        bot = Bot(BOT_TOKEN)
        callback_acks: list[str] = []

        async def acknowledge(
            *, callback_query_id: str, request_timeout: int | None = None
        ) -> bool:
            assert request_timeout == 2
            callback_acks.append(callback_query_id)
            return True

        monkeypatch.setattr(bot, "answer_callback_query", acknowledge)

        class RejectProtectedLookup:
            async def resolve(
                self, session: AsyncSession, *, bot_id: int, telegram_user_id: int
            ) -> UUID:
                raise AssertionError("unbound or group callback accessed owner mapping")

        client = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=bot,
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                    owner_resolver=RejectProtectedLookup(),
                )
            )
        )
        try:
            await client.start_server()
            updates = [
                {
                    "update_id": 9_000_003,
                    "message": {
                        "message_id": 1,
                        "date": 1_760_000_000,
                        "chat": {"id": -100123, "type": "supergroup"},
                        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                        "text": "private data request",
                    },
                },
                {
                    "update_id": 9_000_004,
                    "callback_query": {
                        "id": "forged-chat",
                        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                        "chat_instance": "instance",
                        "message": {
                            "message_id": 2,
                            "date": 1_760_000_000,
                            "chat": {"id": user_id + 1, "type": "private"},
                        },
                        "data": "opaque-token",
                    },
                },
                {
                    "update_id": 9_000_011,
                    "callback_query": {
                        "id": "group-callback",
                        "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                        "chat_instance": "group-instance",
                        "message": {
                            "message_id": 3,
                            "date": 1_760_000_000,
                            "chat": {"id": -100123, "type": "supergroup"},
                        },
                        "data": "opaque-token",
                    },
                },
            ]
            for payload in updates:
                response = await client.post(
                    "/telegram/webhook", json=payload, headers={SECRET_HEADER: WEBHOOK_SECRET}
                )
                assert response.status == 200
            assert callback_acks == ["forged-chat", "group-callback"]
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(InboxUpdate)
                        .where(
                            InboxUpdate.bot_id == bot_id,
                            InboxUpdate.update_id.in_([9_000_003, 9_000_004, 9_000_011]),
                        )
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TelegramOwnerRecord)
                        .where(
                            TelegramOwnerRecord.bot_id == bot_id,
                            TelegramOwnerRecord.telegram_user_id == user_id,
                        )
                    )
                    == 0
                )
        finally:
            await client.close()

    run_local_async(scenario)


def test_webhook_callback_ack_waits_for_committed_receipt(
    ingress_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id, update_id = _bot_id(), 420_006, 9_000_006
        bot = Bot(BOT_TOKEN)
        acknowledgements: list[str] = []

        async def acknowledge(
            *, callback_query_id: str, request_timeout: int | None = None
        ) -> bool:
            assert request_timeout == 2
            async with sessions() as session:
                persisted = await session.scalar(
                    select(func.count())
                    .select_from(InboxUpdate)
                    .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                )
            assert persisted == 1
            acknowledgements.append(callback_query_id)
            return True

        monkeypatch.setattr(bot, "answer_callback_query", acknowledge)
        client = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=bot,
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                )
            )
        )
        callback = {
            "update_id": update_id,
            "callback_query": {
                "id": "committed-first",
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat_instance": "instance",
                "message": {
                    "message_id": update_id,
                    "date": 1_760_000_000,
                    "chat": {"id": user_id, "type": "private"},
                },
                "data": "opaque-token",
            },
        }
        try:
            await client.start_server()
            response = await client.post(
                "/telegram/webhook", json=callback, headers={SECRET_HEADER: WEBHOOK_SECRET}
            )
            assert response.status == 200
            assert acknowledgements == ["committed-first"]
        finally:
            await client.close()

    run_local_async(scenario)


@pytest.mark.parametrize("ack_failure", ["expired", "timeout"])
def test_webhook_committed_callback_is_accepted_when_ack_fails(
    ingress_database: str, monkeypatch: pytest.MonkeyPatch, ack_failure: str
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id, update_id = _bot_id(), 420_010, 9_000_022
        callback_id = f"failed-ack-{uuid4().hex}"
        bot = Bot(BOT_TOKEN)

        async def fail_ack(*, callback_query_id: str, request_timeout: int | None = None) -> bool:
            assert callback_query_id == callback_id
            assert request_timeout == 2
            method = AnswerCallbackQuery(callback_query_id=callback_query_id)
            if ack_failure == "expired":
                raise TelegramBadRequest(method=method, message="query is too old")
            raise TelegramNetworkError(method=method, message="request timed out")

        monkeypatch.setattr(bot, "answer_callback_query", fail_ack)
        client = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=bot,
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                )
            )
        )
        callback = {
            "update_id": update_id,
            "callback_query": {
                "id": callback_id,
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat_instance": "instance",
                "message": {
                    "message_id": update_id,
                    "date": 1_760_000_000,
                    "chat": {"id": user_id, "type": "private"},
                },
                "data": "opaque-token",
            },
        }
        try:
            await client.start_server()
            response = await client.post(
                "/telegram/webhook", json=callback, headers={SECRET_HEADER: WEBHOOK_SECRET}
            )
            assert response.status == 200
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(InboxUpdate)
                        .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                    )
                    == 1
                )
        finally:
            await client.close()

    run_local_async(scenario)


def test_payment_query_uses_unordered_high_priority_lane_while_speech_is_leased(
    ingress_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id = _bot_id(), 420_007

        class FakeSession:
            async def close(self) -> None:
                return None

        class RejectingPreCheckoutBot:
            answers: list[dict[str, object]] = []
            session = FakeSession()

            async def answer_pre_checkout_query(
                self,
                *,
                pre_checkout_query_id: str,
                ok: bool,
                error_message: str | None,
                request_timeout: int,
            ) -> bool:
                self.answers.append(
                    {
                        "pre_checkout_query_id": pre_checkout_query_id,
                        "ok": ok,
                        "error_message": error_message,
                        "request_timeout": request_timeout,
                    }
                )
                return True

        bot = RejectingPreCheckoutBot()
        client = TestClient(
            TestServer(
                create_webhook_app(
                    engine=engine,
                    sessions=sessions,
                    bot=cast(Bot, bot),
                    bot_id=bot_id,
                    webhook_secret=WEBHOOK_SECRET,
                )
            )
        )
        try:
            await client.start_server()
            speech = {
                "update_id": 9_000_007,
                "message": {
                    "message_id": 9_000_008,
                    "date": 1_760_000_000,
                    "chat": {"id": user_id, "type": "private"},
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "voice": {"file_id": "f", "file_unique_id": "u", "duration": 3},
                },
            }
            payment_query = {
                "update_id": 9_000_009,
                "pre_checkout_query": {
                    "id": "query-1",
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "currency": "XTR",
                    "total_amount": 1,
                    "invoice_payload": "opaque-invoice",
                },
            }
            for payload in (speech, payment_query):
                response = await client.post(
                    "/telegram/webhook", json=payload, headers={SECRET_HEADER: WEBHOOK_SECRET}
                )
                assert response.status == 200
            assert len(bot.answers) == 1
            answer = bot.answers[0]
            assert answer["pre_checkout_query_id"] == "query-1"
            assert answer["ok"] is False
            assert answer["error_message"] == (
                "This payment order is invalid. Open /subscription to start again."
            )
            async with sessions() as session, session.begin():
                speech_claim = await claim_job(
                    session,
                    lane=JobLane.SPEECH,
                    worker_id="ingress-speech-lane",
                    lease_for=timedelta(minutes=1),
                )
                assert speech_claim is not None
            async with sessions() as session:
                payment_job = await session.scalar(
                    select(Job).where(Job.deduplication_key == f"telegram:{bot_id}:9000009")
                )
                assert payment_job is not None
                assert payment_job.lane == JobLane.APPLICATION.value
                assert payment_job.conversation_sequence is None
                assert payment_job.priority == 100
            async with sessions() as session, session.begin():
                from pin.platform.jobs import _claim_eligible

                payment_is_claimable = await session.scalar(
                    select(Job.id)
                    .where(*_claim_eligible(JobLane.APPLICATION), Job.id == payment_job.id)
                    .limit(1)
                )
                assert payment_is_claimable == payment_job.id
        finally:
            await client.close()

    run_local_async(scenario)


def test_polling_persists_before_advancing_offset_and_restarts_from_durable_offset(
    ingress_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id, update_id = _bot_id(), 420_004, 9_000_005
        callback_seen_durable = asyncio.Event()

        class FakePollBot:
            def __init__(self, batches: list[list[Update]]) -> None:
                self.batches = list(batches)
                self.offsets: list[int] = []
                self.on_answer: Callable[[], Awaitable[None]] | None = None
                self.on_poll: Callable[[], Awaitable[None]] | None = None

            async def get_updates(
                self, *, offset: int, timeout: int, allowed_updates: list[str]
            ) -> list[Update]:
                self.offsets.append(offset)
                if self.on_poll is not None:
                    await self.on_poll()
                return self.batches.pop(0) if self.batches else []

            async def answer_callback_query(
                self, *, callback_query_id: str, request_timeout: int | None = None
            ) -> bool:
                assert request_timeout == 2
                assert callback_query_id == "durable-callback"
                if self.on_answer is not None:
                    await self.on_answer()
                return True

            async def verify_durable_receipt(self) -> None:
                async with sessions() as session:
                    persisted = await session.scalar(
                        select(func.count())
                        .select_from(InboxUpdate)
                        .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                    )
                    assert persisted == 1
                    offset = await session.scalar(
                        select(TelegramIngressBotRecord.next_update_offset).where(
                            TelegramIngressBotRecord.bot_id == bot_id
                        )
                    )
                    assert offset == 0
                callback_seen_durable.set()

        raw_update = {
            "update_id": update_id,
            "callback_query": {
                "id": "durable-callback",
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat_instance": "instance",
                "message": {
                    "message_id": update_id,
                    "date": 1_760_000_000,
                    "chat": {"id": user_id, "type": "private"},
                },
                "data": "opaque-token",
            },
        }
        update = Update.model_validate(raw_update)
        first_bot = FakePollBot([[update], []])
        stop = asyncio.Event()

        async def stop_after_callback() -> None:
            await first_bot.verify_durable_receipt()
            stop.set()

        first_bot.on_answer = stop_after_callback
        try:
            await poll_updates(
                engine=engine,
                sessions=sessions,
                bot=cast(Bot, first_bot),
                bot_id=bot_id,
                stop=stop,
            )
            assert callback_seen_durable.is_set()
            assert first_bot.offsets == [0]
            async with sessions() as session:
                offset = await session.scalar(
                    select(TelegramIngressBotRecord.next_update_offset).where(
                        TelegramIngressBotRecord.bot_id == bot_id
                    )
                )
                assert offset == update_id + 1

            restart_stop = asyncio.Event()
            restart_bot = FakePollBot([[]])

            async def stop_after_poll() -> None:
                restart_stop.set()

            restart_bot.on_poll = stop_after_poll
            await poll_updates(
                engine=engine,
                sessions=sessions,
                bot=cast(Bot, restart_bot),
                bot_id=bot_id,
                stop=restart_stop,
            )
            assert restart_bot.offsets == [update_id + 1]
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize("ack_failure", ["expired", "timeout"])
def test_polling_callback_ack_failure_cannot_hold_later_payment_update_or_offset(
    ingress_database: str, ack_failure: str
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id = _bot_id(), 420_009
        callback_id = f"expired-{uuid4().hex}"
        callback_update_id, payment_update_id = 9_000_020, 9_000_021
        callback = Update.model_validate(
            {
                "update_id": callback_update_id,
                "callback_query": {
                    "id": callback_id,
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "chat_instance": "instance",
                    "message": {
                        "message_id": callback_update_id,
                        "date": 1_760_000_000,
                        "chat": {"id": user_id, "type": "private"},
                    },
                    "data": "expired-token",
                },
            }
        )
        payment_query_id = f"checkout-{uuid4().hex}"
        payment = Update.model_validate(
            {
                "update_id": payment_update_id,
                "pre_checkout_query": {
                    "id": payment_query_id,
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "currency": "XTR",
                    "total_amount": 1,
                    "invoice_payload": "opaque-invoice",
                },
            }
        )
        stop = asyncio.Event()

        class FailingAckBot:
            offsets: list[int] = []
            request_timeouts: list[int | None] = []
            precheckout_answers: list[dict[str, object]] = []
            supplied = False
            get_updates_calls = 0

            async def get_updates(
                self, *, offset: int, timeout: int, allowed_updates: list[str]
            ) -> list[Update]:
                self.get_updates_calls += 1
                self.offsets.append(offset)
                if not self.supplied:
                    self.supplied = True
                    return [callback, payment]
                if offset == payment_update_id + 1:
                    stop.set()
                if self.get_updates_calls >= 3:
                    stop.set()
                await asyncio.sleep(0)
                return []

            async def answer_callback_query(
                self, *, callback_query_id: str, request_timeout: int | None = None
            ) -> bool:
                assert callback_query_id == callback_id
                self.request_timeouts.append(request_timeout)
                method = AnswerCallbackQuery(callback_query_id=callback_query_id)
                if ack_failure == "expired":
                    raise TelegramBadRequest(method=method, message="query is too old")
                raise TelegramNetworkError(method=method, message="request timed out")

            async def answer_pre_checkout_query(
                self,
                *,
                pre_checkout_query_id: str,
                ok: bool,
                error_message: str | None,
                request_timeout: int,
            ) -> bool:
                self.precheckout_answers.append(
                    {
                        "pre_checkout_query_id": pre_checkout_query_id,
                        "ok": ok,
                        "error_message": error_message,
                        "request_timeout": request_timeout,
                        "response": True,
                    }
                )
                return True

        bot = FailingAckBot()
        try:
            await poll_updates(
                engine=engine,
                sessions=sessions,
                bot=cast(Bot, bot),
                bot_id=bot_id,
                stop=stop,
            )
            assert bot.offsets == [0, payment_update_id + 1]
            assert bot.get_updates_calls == 2
            assert bot.request_timeouts == [2]
            assert len(bot.precheckout_answers) == 1
            answer = bot.precheckout_answers[0]
            assert answer["pre_checkout_query_id"] == payment_query_id
            assert answer["ok"] is False
            assert answer["error_message"] == (
                "This payment order is invalid. Open /subscription to start again."
            )
            assert isinstance(answer["request_timeout"], int)
            assert answer["response"] is True
            async with sessions() as session:
                inboxes = list(
                    (
                        await session.scalars(
                            select(InboxUpdate).where(
                                InboxUpdate.bot_id == bot_id,
                                InboxUpdate.update_id.in_([callback_update_id, payment_update_id]),
                            )
                        )
                    ).all()
                )
                assert {row.update_id for row in inboxes} == {
                    callback_update_id,
                    payment_update_id,
                }
                payment_job = await session.scalar(
                    select(Job).where(
                        Job.deduplication_key == f"telegram:{bot_id}:{payment_update_id}"
                    )
                )
                assert payment_job is not None
                assert payment_job.lane == JobLane.APPLICATION.value
                assert payment_job.conversation_sequence is None
                assert payment_job.priority == 100
                offset = await session.scalar(
                    select(TelegramIngressBotRecord.next_update_offset).where(
                        TelegramIngressBotRecord.bot_id == bot_id
                    )
                )
                assert offset == payment_update_id + 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_polling_persistence_failure_keeps_offset_for_telegram_retry(
    ingress_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id, user_id, update_id = _bot_id(), 420_008, 9_000_010
        suffix = uuid4().hex[:12]
        function_name, trigger_name = f"reject_poll_{suffix}", f"reject_poll_{suffix}"
        raw_update = Update.model_validate(
            {
                "update_id": update_id,
                "message": {
                    "message_id": update_id + 1,
                    "date": 1_760_000_000,
                    "chat": {"id": user_id, "type": "private"},
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "text": "retry me",
                },
            }
        )

        class OneUpdateBot:
            async def get_updates(
                self, *, offset: int, timeout: int, allowed_updates: list[str]
            ) -> list[Update]:
                assert offset == 0
                return [raw_update]

        stop = asyncio.Event()

        async def stop_after_retry(delay: float) -> None:
            assert delay == 1
            stop.set()

        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION pin_telegram.{function_name}() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN "
                        f"IF NEW.bot_id = {bot_id} THEN "
                        "RAISE EXCEPTION 'test poll persistence failure'; "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON platform.inbox "
                        f"FOR EACH ROW EXECUTE FUNCTION pin_telegram.{function_name}()"
                    )
                )
            await poll_updates(
                engine=engine,
                sessions=sessions,
                bot=cast(Bot, OneUpdateBot()),
                bot_id=bot_id,
                stop=stop,
                sleep=stop_after_retry,
            )
            async with sessions() as session:
                offset = await session.scalar(
                    select(TelegramIngressBotRecord.next_update_offset).where(
                        TelegramIngressBotRecord.bot_id == bot_id
                    )
                )
                receipt_count = await session.scalar(
                    select(func.count())
                    .select_from(InboxUpdate)
                    .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                )
                owner_count = await session.scalar(
                    select(func.count())
                    .select_from(TelegramOwnerRecord)
                    .where(
                        TelegramOwnerRecord.bot_id == bot_id,
                        TelegramOwnerRecord.telegram_user_id == user_id,
                    )
                )
                assert offset == 0 and receipt_count == owner_count == 0
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"DROP TRIGGER IF EXISTS {trigger_name} ON platform.inbox")
                )
                await connection.execute(
                    text(f"DROP FUNCTION IF EXISTS pin_telegram.{function_name}()")
                )
            await engine.dispose()

    run_local_async(scenario)


def test_pollers_are_exclusive_and_mode_cannot_switch(ingress_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(ingress_database)
        sessions = create_session_factory(engine)
        bot_id = _bot_id()
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingPollBot:
            async def get_updates(
                self, *, offset: int, timeout: int, allowed_updates: list[str]
            ) -> list[Update]:
                entered.set()
                await release.wait()
                return []

        stop = asyncio.Event()
        first = asyncio.create_task(
            poll_updates(
                engine=engine,
                sessions=sessions,
                bot=cast(Bot, BlockingPollBot()),
                bot_id=bot_id,
                stop=stop,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(RuntimeError, match="another local poller"):
                await poll_updates(
                    engine=engine,
                    sessions=sessions,
                    bot=cast(Bot, BlockingPollBot()),
                    bot_id=bot_id,
                    stop=asyncio.Event(),
                )
            stop.set()
            release.set()
            await first

            async with sessions() as session, session.begin():
                with pytest.raises(RuntimeError, match="another ingress mode"):
                    await select_ingress_mode(session, bot_id=bot_id, mode="webhook")
        finally:
            if not first.done():
                stop.set()
                release.set()
                await first
            await engine.dispose()

    run_local_async(scenario)
