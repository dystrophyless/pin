from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram import payments as telegram_payments
from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.api import (
    AcceptPaymentTerms,
    ActivatePaidPeriod,
    CheckStarsCheckout,
    CreateStarsOrder,
    FinishStarsRefund,
    PaymentRejected,
    ReadSubscriptionStatus,
    RecordStarsPayment,
    RequestStarsRefund,
    SqlAlchemyAccessUsageStore,
    SqlAlchemyStarsPaymentStore,
    StartTrial,
    ViewPaymentTerms,
)
from pin.contexts.billing.infrastructure.models import (
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.models import InboxUpdate

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue20_payments_"
DATABASE_USER = "pin_test"
DATABASE_PORT = 51017
DATABASE_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def _payment_database_url() -> str:
    value = os.getenv("PIN_TEST_ISSUE20_PAYMENTS_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_ISSUE20_PAYMENTS_DATABASE_URL to an owned issue20 database")
    url = make_url(value)
    if url.host != "127.0.0.1" or url.port != DATABASE_PORT:
        pytest.fail("Issue20 payment proof requires the designated local PostgreSQL cluster")
    if not (url.database or "").startswith(DATABASE_PREFIX):
        pytest.fail("Issue20 payment proof requires its own pin_issue20_payments_ database")
    if url.database == "pin_model_adapter_20261013":
        pytest.fail("Issue20 tests cannot use the protected model adapter database")
    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
            version = str(connection.exec_driver_sql("show server_version").scalar_one())
    finally:
        engine.dispose()
    if name != url.database or user != DATABASE_USER or port != DATABASE_PORT or version != "18.6":
        pytest.fail(
            "Issue20 PostgreSQL does not match its assigned database, user, port and version"
        )
    if directory.replace("\\", "/").rstrip("/").casefold() != DATABASE_DIRECTORY.casefold():
        pytest.fail("Issue20 payment proof is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def payment_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _payment_database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    # The fixture never truncates, resets, or drops the assigned database.
    run_local_async(engine.dispose)


def _payment(
    owner_id,
    telegram_user_id: int,
    payload: str,
    charge_id: str,
    *,
    at: datetime,
    end: datetime,
    first: bool,
) -> RecordStarsPayment:
    return RecordStarsPayment(
        owner_id=owner_id,
        telegram_user_id=telegram_user_id,
        invoice_payload=payload,
        telegram_charge_id=charge_id,
        provider_charge_id=f"provider-{charge_id}",
        currency="XTR",
        total_amount=250,
        is_recurring=True,
        is_first_recurring=first,
        subscription_expiration_date=end,
        at=at,
    )


async def _prepare_order(
    sessions, owner_id, telegram_user_id: int, at: datetime, *, invoice_pending: bool = False
):
    async with sessions() as session, session.begin():
        access = SqlAlchemyAccessUsageStore(session)
        await access.activate_trial(StartTrial(owner_id, telegram_user_id, at))
        payments = SqlAlchemyStarsPaymentStore(session)
        await payments.view_terms(ViewPaymentTerms(owner_id, at))
        await payments.accept_terms(AcceptPaymentTerms(owner_id, at))
        invoice = await payments.create_order(
            CreateStarsOrder(owner_id, telegram_user_id, f"payload-{owner_id}", at)
        )
        if not invoice_pending:
            order_row = await session.get(PaymentOrderRecord, invoice.order_id)
            assert order_row is not None
            order_row.invoice_status = "sent"
            order_row.invoice_message_id = 1
        return invoice



def test_paid_cancel_routes_through_persisted_worker_and_whitespace_is_inert(
    payment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 730_000_000 + owner_id.int % 100_000_000
    bot_id = 987_654 + owner_id.int % 1_000_000
    worker_id = f"issue20-cancel-{uuid4().hex[:12]}"

    async def persist_and_process(update_id: int, text: str) -> None:
        async with payment_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload={
                    "update_id": update_id,
                    "message": {
                        "message_id": update_id,
                        "date": int(NOW.timestamp()),
                        "chat": {"id": telegram_user_id, "type": "private"},
                        "from": {"id": telegram_user_id, "is_bot": False},
                        "text": text,
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted
            mapping = await session.scalar(
                select(TelegramOwnerRecord.owner_id).where(
                    TelegramOwnerRecord.bot_id == bot_id,
                    TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                )
            )
            assert mapping == owner_id
        claim = await claim_application_job(payment_sessions, worker_id=worker_id)
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(payment_sessions, claim, now=NOW, assistant=None)

    async def scenario() -> None:
        invoice = await _prepare_order(payment_sessions, owner_id, telegram_user_id, NOW)
        async with payment_sessions() as session, session.begin():
            checkout = await SqlAlchemyStarsPaymentStore(session).check_checkout(
                CheckStarsCheckout(
                    owner_id,
                    telegram_user_id,
                    invoice.payload,
                    f"route-checkout-{uuid4().hex}",
                    "XTR",
                    250,
                    NOW,
                )
            )
            assert checkout.ok
            accepted = await SqlAlchemyStarsPaymentStore(session).record_payment(
                _payment(
                    owner_id,
                    telegram_user_id,
                    invoice.payload,
                    f"route-cancel-{uuid4().hex}",
                    at=NOW,
                    end=NOW + timedelta(days=30),
                    first=True,
                )
            )
            assert accepted
            saved = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(language="en", home_zone="UTC"),
            )
            assert saved is not None

        await persist_and_process(1, "/cancel")
        async with payment_sessions() as session:
            order = await session.get(PaymentOrderRecord, invoice.order_id)
            assert order is not None and order.cancel_status == "queued"
            paid_periods_before = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert paid_periods_before == 1

        await persist_and_process(2, "   \t  ")
        async with payment_sessions() as session:
            order = await session.get(PaymentOrderRecord, invoice.order_id)
            assert order is not None and order.cancel_status == "queued"
            assert order.cancel_requested_at == NOW
            paid_periods_after = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert paid_periods_after == paid_periods_before

        # Consume only this fixture's queued request locally so later tests cannot
        # mistake it for their provider-work item. No provider is contacted.
        async with payment_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            cancellation = await payments.claim_cancellation(at=NOW)
            assert cancellation is not None and cancellation.order_id == invoice.order_id
            assert await payments.finish_cancellation(
                order_id=invoice.order_id,
                status="failed",
                at=NOW + timedelta(seconds=1),
            )

    run_local_async(scenario)

def test_payment_receipt_replay_refund_and_cancel_preserve_independent_coverage(
    payment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 730_000_000 + owner_id.int % 100_000_000
    first_end = NOW + timedelta(days=30)
    second_end = NOW + timedelta(days=60)

    async def scenario() -> None:
        invoice = await _prepare_order(payment_sessions, owner_id, telegram_user_id, NOW)
        async with payment_sessions() as session, session.begin():
            decision = await SqlAlchemyStarsPaymentStore(session).check_checkout(
                CheckStarsCheckout(
                    owner_id,
                    telegram_user_id,
                    invoice.payload,
                    f"checkout-{uuid4()}",
                    "XTR",
                    250,
                    NOW,
                )
            )
            assert decision.ok
        first = _payment(
            owner_id,
            telegram_user_id,
            invoice.payload,
            "charge-1",
            at=NOW,
            end=first_end,
            first=True,
        )

        async def record_first_receipt() -> bool:
            async with payment_sessions() as session, session.begin():
                return await SqlAlchemyStarsPaymentStore(session).record_payment(first)

        concurrent_results = await asyncio.gather(record_first_receipt(), record_first_receipt())
        assert concurrent_results == [True, True]

        async def begin_first_refund() -> tuple[int, str]:
            async with payment_sessions() as session, session.begin():
                return await SqlAlchemyStarsPaymentStore(session).begin_refund(
                    RequestStarsRefund("charge-1", "test-operator", NOW)
                )

        replayed, refund_target = await asyncio.gather(record_first_receipt(), begin_first_refund())
        assert replayed
        assert refund_target == (telegram_user_id, "charge-1")
        async with payment_sessions() as session, session.begin():
            renewal = _payment(
                owner_id,
                telegram_user_id,
                invoice.payload,
                "charge-2",
                at=first_end,
                end=second_end,
                first=False,
            )
            assert await SqlAlchemyStarsPaymentStore(session).record_payment(renewal)
        async with payment_sessions() as session, session.begin():
            with pytest.raises(PaymentRejected, match="out of order"):
                await SqlAlchemyStarsPaymentStore(session).record_payment(
                    _payment(
                        owner_id,
                        telegram_user_id,
                        invoice.payload,
                        "charge-old",
                        at=first_end,
                        end=first_end + timedelta(days=15),
                        first=False,
                    )
                )
        async with payment_sessions() as session, session.begin():
            assert await SqlAlchemyStarsPaymentStore(session).finish_refund(
                FinishStarsRefund("charge-1", "confirmed", first_end + timedelta(seconds=1))
            )
        async with payment_sessions() as session, session.begin():
            assert await SqlAlchemyStarsPaymentStore(session).record_payment(first)
            periods = list(
                (
                    await session.scalars(
                        select(AccessPeriodRecord).where(
                            AccessPeriodRecord.owner_id == owner_id,
                            AccessPeriodRecord.policy_kind == "paid",
                        )
                    )
                ).all()
            )
            assert len(periods) == 1
            assert periods[0].ends_at == second_end
            refunded = await session.get(PaymentReceiptRecord, "charge-1")
            assert refunded is not None and refunded.refunded_at is not None
            payment_store = SqlAlchemyStarsPaymentStore(session)
            assert (
                await payment_store.request_cancellation(owner_id=owner_id, at=first_end)
                == "pending"
            )
            cancellation = await payment_store.claim_cancellation(at=first_end)
            assert cancellation is not None and cancellation.first_charge_id == "charge-1"
            assert await payment_store.finish_cancellation(
                order_id=cancellation.order_id,
                status="confirmed",
                at=first_end + timedelta(seconds=2),
            )
            status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, first_end + timedelta(days=1))
            )
            assert status.active
            assert status.ends_at == second_end
            assert status.ai_turns_remaining == 1000
            assert status.voice_seconds_remaining == 3600
            count = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert count == 1

    run_local_async(scenario)


def test_invalid_checkout_and_receipt_never_grant_paid_access(
    payment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 740_000_000 + owner_id.int % 100_000_000

    async def scenario() -> None:
        invoice = await _prepare_order(payment_sessions, owner_id, telegram_user_id, NOW)
        async with payment_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            denied = await payments.check_checkout(
                CheckStarsCheckout(
                    owner_id,
                    telegram_user_id,
                    invoice.payload,
                    f"wrong-price-{uuid4()}",
                    "XTR",
                    249,
                    NOW,
                )
            )
            assert not denied.ok
        async with payment_sessions() as session, session.begin():
            with pytest.raises(PaymentRejected, match="did not follow an approved checkout"):
                await SqlAlchemyStarsPaymentStore(session).record_payment(
                    _payment(
                        owner_id,
                        telegram_user_id,
                        invoice.payload,
                        "charge-unapproved",
                        at=NOW,
                        end=NOW + timedelta(days=30),
                        first=True,
                    )
                )
        async with payment_sessions() as session, session.begin():
            count = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert count == 0

    run_local_async(scenario)


def test_scheduled_paid_coverage_blocks_checkout_and_another_order(
    payment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 750_000_000 + owner_id.int % 100_000_000

    async def scenario() -> None:
        invoice = await _prepare_order(payment_sessions, owner_id, telegram_user_id, NOW)
        async with payment_sessions() as session, session.begin():
            scheduled_start = NOW + timedelta(days=30)
            assert await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(
                    owner_id,
                    "scheduled-paid-period",
                    scheduled_start,
                    scheduled_start + timedelta(days=30),
                )
            )
        async with payment_sessions() as session, session.begin():
            decision = await SqlAlchemyStarsPaymentStore(session).check_checkout(
                CheckStarsCheckout(
                    owner_id,
                    telegram_user_id,
                    invoice.payload,
                    f"checkout-scheduled-{uuid4()}",
                    "XTR",
                    250,
                    NOW,
                )
            )
            assert not decision.ok
        async with payment_sessions() as session, session.begin():
            with pytest.raises(PaymentRejected, match="active or scheduled"):
                await SqlAlchemyStarsPaymentStore(session).create_order(
                    CreateStarsOrder(owner_id, telegram_user_id, "second-payload", NOW)
                )

    run_local_async(scenario)


def test_worker_restart_fences_stale_invoice_claim_without_resending(
    payment_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 760_000_000 + owner_id.int % 100_000_000

    async def scenario() -> None:
        await _prepare_order(
            payment_sessions, owner_id, telegram_user_id, NOW, invoice_pending=True
        )
        async with payment_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            claimed = await payments.claim_invoice(at=NOW)
            assert claimed is not None and claimed.invoice_status == "sending"
        async with payment_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            assert (
                await payments.recover_uncertain_provider_calls(
                    at=NOW + timedelta(minutes=3),
                    lease_for=timedelta(minutes=2),
                )
                == 1
            )
        async with payment_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            assert await payments.claim_invoice(at=NOW + timedelta(minutes=4)) is None
            order_row = await session.scalar(
                select(PaymentOrderRecord).where(PaymentOrderRecord.owner_id == owner_id)
            )
            assert order_row is not None
            assert order_row.invoice_status == "unknown"
            assert order_row.invoice_error_code == "provider_outcome_unknown_after_worker_restart"

    run_local_async(scenario)


def test_precheckout_lock_timeout_denies_and_keeps_persisted_update(
    payment_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid4()
    telegram_user_id = 770_000_000 + owner_id.int % 100_000_000
    bot_id = 77_020
    update_id = 990_000 + owner_id.int % 100_000
    query_id = f"precheckout-{owner_id}"

    class AnswerBot:
        def __init__(self) -> None:
            self.ok: bool | None = None

        async def answer_pre_checkout_query(
            self,
            *,
            pre_checkout_query_id: str,
            ok: bool,
            error_message: str | None,
            request_timeout: int,
        ) -> bool:
            assert pre_checkout_query_id == query_id
            if not ok:
                assert error_message
            else:
                assert error_message is None
            assert 0 < request_timeout <= 10
            self.ok = ok
            return True

    bot = AnswerBot()
    monkeypatch.setattr(telegram_payments, "PRECHECKOUT_DEADLINE_SECONDS", 0.6)
    monkeypatch.setattr(telegram_payments, "PRECHECKOUT_ANSWER_RESERVE_SECONDS", 0.2)

    async def scenario() -> None:
        invoice = await _prepare_order(payment_sessions, owner_id, telegram_user_id, NOW)
        async with payment_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    owner_id=owner_id,
                )
            )
        update = {
            "update_id": update_id,
            "pre_checkout_query": {
                "id": query_id,
                "from": {"id": telegram_user_id},
                "invoice_payload": invoice.payload,
                "currency": "XTR",
                "total_amount": 250,
            },
        }
        async with payment_sessions() as session, session.begin():
            persisted = await persist_update(
                session,
                bot_id=bot_id,
                payload=update,
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert persisted is not None and persisted.accepted

        loop = asyncio.get_running_loop()
        async with payment_sessions() as lock_session, lock_session.begin():
            await lock_session.scalar(
                select(PaymentOrderRecord)
                .where(PaymentOrderRecord.owner_id == owner_id)
                .with_for_update()
            )
            started = loop.time()
            approved = await telegram_payments.answer_pre_checkout(
                payment_sessions,
                bot,  # type: ignore[arg-type]
                bot_id=bot_id,
                update=update,
                now=NOW,
                received_at=started,
            )
            assert approved
            assert bot.ok is False
            assert loop.time() - started < 1.5
            async with payment_sessions() as verify_session:
                retained = await verify_session.scalar(
                    select(InboxUpdate).where(
                        InboxUpdate.bot_id == bot_id,
                        InboxUpdate.update_id == update_id,
                    )
                )
                assert retained is not None
                order = await verify_session.scalar(
                    select(PaymentOrderRecord).where(PaymentOrderRecord.owner_id == owner_id)
                )
                assert order is not None and order.pre_checkout_query_id is None
                paid_periods = await verify_session.scalar(
                    select(func.count())
                    .select_from(AccessPeriodRecord)
                    .where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == "paid",
                    )
                )
                assert paid_periods == 0

    run_local_async(scenario)
