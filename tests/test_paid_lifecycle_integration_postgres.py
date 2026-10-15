from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.api import (
    AcceptPaymentTerms,
    CheckStarsCheckout,
    CreateStarsOrder,
    FinishStarsRefund,
    Operation,
    ReadSubscriptionStatus,
    RecordStarsPayment,
    RequestStarsRefund,
    ReserveUsage,
    SqlAlchemyAccessUsageStore,
    SqlAlchemyStarsPaymentStore,
    StartTrial,
    TrialActivationStatus,
    ViewPaymentTerms,
)
from pin.contexts.billing.domain.access import PeriodKind, PlanPolicy
from pin.contexts.billing.domain.payments import STARS_CURRENCY, STARS_PERIOD, STARS_PRICE
from pin.contexts.billing.infrastructure.models import (
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import ReminderSend, TelegramReplySend
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.api import (
    ConfirmationGrant,
    ConfirmationProof,
    ConfirmedPlanBatch,
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
    create_confirmed_plan_batch,
)
from pin.contexts.planning.infrastructure.models import PlanItemRecord
from pin.contexts.planning.infrastructure.store import SqlAlchemyPlanningStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.models import InboxUpdate, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue26_paid_"
DATABASE_USER = "pin_test"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
BOT_ID = 8_100_026
TELEGRAM_USER_ID = 8_200_026
CANCEL_BOT_ID = 8_100_027
CANCEL_TELEGRAM_USER_ID = 8_200_027


def _database_url() -> str:
    value = os.getenv("PIN_TEST_ISSUE26_PAID_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_ISSUE26_PAID_DATABASE_URL to the assigned fresh fixture")
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("Issue26 proof requires the psycopg PostgreSQL driver")
    if (url.host, url.port, url.username) != ("127.0.0.1", DATABASE_PORT, DATABASE_USER):
        pytest.fail("Issue26 proof refuses a different PostgreSQL endpoint or user")
    if not url.database or not url.database.startswith(DATABASE_PREFIX):
        pytest.fail("Issue26 proof refuses a database outside pin_issue26_paid_")
    guard_engine = create_sync_engine(value)
    try:
        with guard_engine.connect() as connection:
            database = str(connection.scalar(text("select current_database()")))
            user = str(connection.scalar(text("select current_user")))
            port = int(connection.scalar(text("show port")))
            directory = str(connection.scalar(text("show data_directory")))
            version = str(connection.scalar(text("show server_version")))
    finally:
        guard_engine.dispose()
    if (database, user, port, version) != (url.database, DATABASE_USER, DATABASE_PORT, "18.6"):
        pytest.fail("Issue26 PostgreSQL database identity does not match the assigned cluster")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("Issue26 proof refuses an unassigned PostgreSQL data directory")
    return value


@pytest.fixture(scope="session")
def paid_lifecycle_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    run_local_async(engine.dispose)


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class _ConfirmedLocalPreview:
    """Inject one trusted user-confirmation grant without a Telegram interaction."""

    def __init__(self, proof: ConfirmationProof) -> None:
        self.proof = proof
        self.consumed = False

    async def verify_and_consume(
        self,
        proof: ConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> ConfirmationGrant | None:
        if self.consumed or proof != self.proof:
            return None
        self.consumed = True
        return ConfirmationGrant(
            owner_id=owner_id,
            preview_id=proof.preview_id,
            preview_version=proof.preview_version,
            producer_id="issue26-local-confirmation-fixture",
            approved_projection_sha256=approved_projection_sha256,
            issued_at=at,
            expires_at=at + timedelta(minutes=5),
        )


class _LocalDeliveryTransport:
    """Record fake reminder sends while resolving the real persisted Telegram owner."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.replies = 0
        self.cancelled: list[tuple[int, str]] = []
        self.invoices: list[tuple[int, str, str]] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        value = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )
        return int(value) if value is not None else None

    async def send_reminder(self, message: ReminderSend) -> str:
        attempt_id = f"issue26-fake-send-{len(self.sent) + 1}"
        self.sent.append((message.destination_id, attempt_id))
        return attempt_id

    async def send_reply(self, _message: TelegramReplySend) -> str:
        self.replies += 1
        return f"issue26-fake-reply-{self.replies}"

    async def send_stars_invoice(self, *, chat_id: int, payload: str, language: str) -> int:
        self.invoices.append((chat_id, payload, language))
        return 8_600_026 + len(self.invoices)

    async def cancel_stars_renewal(self, *, user_id: int, first_charge_id: str) -> bool:
        self.cancelled.append((user_id, first_charge_id))
        return True


def _landing_value(language: str, key: str) -> str:
    source = (ROOT / "landing" / "app.js").read_text(encoding="utf-8")
    start = source.index(f"  {language}: {{")
    next_language = source.find("  en: {", start + 1)
    end = next_language if next_language >= 0 else source.index("\n};", start)
    match = re.search(rf"\b{re.escape(key)}:\s*'([^']*)'", source[start:end])
    assert match is not None, f"landing copy key {language}.{key} is missing"
    return match.group(1)


def _synthetic_receipt(
    *,
    owner_id: UUID,
    telegram_user_id: int,
    payload: str,
    charge_id: str,
    at: datetime,
    is_first_recurring: bool = True,
) -> RecordStarsPayment:
    return RecordStarsPayment(
        owner_id=owner_id,
        telegram_user_id=telegram_user_id,
        invoice_payload=payload,
        telegram_charge_id=charge_id,
        provider_charge_id=f"synthetic-provider-{charge_id}",
        currency=STARS_CURRENCY,
        total_amount=STARS_PRICE,
        is_recurring=True,
        is_first_recurring=is_first_recurring,
        subscription_expiration_date=at + STARS_PERIOD,
        at=at,
    )


def _assert_landing_matches_billing() -> None:
    trial, paid = PlanPolicy.trial(), PlanPolicy.paid()
    assert "seven days" in _landing_value("en", "termsTrial")
    assert f"{trial.allowance.ai_turns} AI interactions" in _landing_value("en", "termsTrial")
    assert f"{trial.allowance.voice_seconds // 60} minutes" in _landing_value("en", "termsTrial")
    assert (
        f"{STARS_PRICE} Stars per {paid.duration.days} days"
        in _landing_value("en", "termsTrial")
    )
    assert f"{paid.allowance.ai_turns:,}" in _landing_value("en", "paidTurns")
    assert f"{paid.allowance.voice_seconds // 60} minutes" in _landing_value("en", "paidVoice")
    assert "not yet verified" in _landing_value("en", "offerStatus")


def test_trial_paid_quota_and_reminders_agree_across_local_runtime(
    paid_lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _assert_landing_matches_billing()
    base = datetime(2026, 10, 15, 9, tzinfo=UTC)
    clock = _Clock(base)
    transport = _LocalDeliveryTransport()
    owner_id: UUID
    trial_period_id: str
    paid_period_id: str
    reminder_ids: dict[str, UUID]

    async def scenario() -> None:
        nonlocal owner_id, trial_period_id, paid_period_id, reminder_ids
        async with paid_lifecycle_sessions() as session, session.begin():
            inbox_result = await persist_update(
                session,
                bot_id=BOT_ID,
                payload={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": int(base.timestamp()),
                        "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                        "from": {"id": TELEGRAM_USER_ID, "is_bot": False},
                        "text": "/subscription",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert inbox_result is not None and inbox_result.accepted
            inbox = await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == BOT_ID,
                    InboxUpdate.update_id == 1,
                )
            )
            assert inbox is not None and inbox.owner_id is not None
            owner_id = inbox.owner_id
            trial = await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, TELEGRAM_USER_ID, base)
            )
            assert trial.status is TrialActivationStatus.ACTIVATED
            assert trial.period_id is not None
            trial_period_id = trial.period_id
            preferences = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=0,
                    reminder_elapsed_hours=0,
                ),
            )
            assert preferences is not None

        drafts = tuple(
            OneOffPlanDraft(
                kind=PlanKind.TASK,
                title=title,
                local_date=(base + timedelta(days=event_day)).date(),
                local_time=time(12),
                event_zone="UTC",
                reminders=(ReminderOffset(OffsetKind.ELAPSED_SECONDS, 25 * 60 * 60),),
            )
            for title, event_day in (
                ("Trial reminder", 2),
                ("Paid reminder", 6),
                ("Renewed reminder", 33),
                ("Expired reminder", 63),
            )
        )
        proof = ConfirmationProof(uuid4(), 1, f"issue26-{uuid4().hex}")
        async with paid_lifecycle_sessions() as session, session.begin():
            identity = SqlAlchemyIdentityStore(session)
            current = await identity.get_preferences(owner_id)
            assert current is not None
            result = await create_confirmed_plan_batch(
                ConfirmedPlanBatch("issue26-plan-batch", current.version, drafts, proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemyPlanningStore(session),
                identity=identity,
                billing=SqlAlchemyAccessUsageStore(session),
                confirmation_verifier=_ConfirmedLocalPreview(proof),
                clock=clock,
            )
            assert len(cast(list[object], result["plans"])) == 4

        assert await process_pending_reminder_schedules(
            paid_lifecycle_sessions, now=base, limit=1000
        ) >= 4

        usage = PlanPolicy.trial().allowance.ai_turns

        async def exhaust_ai(period_at: datetime, allowance: int, prefix: str) -> None:
            async with paid_lifecycle_sessions() as session, session.begin():
                billing = SqlAlchemyAccessUsageStore(session)
                for index in range(allowance):
                    request_id = f"{prefix}-{index:04d}"
                    reserved = await billing.reserve_usage(
                        ReserveUsage(
                            owner_id,
                            request_id,
                            Operation.AI_TURN,
                            1,
                            0,
                            period_at,
                            period_at + timedelta(minutes=5),
                        )
                    )
                    assert reserved.access.allowed and reserved.reservation is not None
                    await billing.settle_usage(
                        owner_id=owner_id,
                        request_id=request_id,
                        ai_turns=1,
                        voice_seconds=0,
                        at=period_at,
                    )

        await exhaust_ai(base + timedelta(seconds=1), usage, "trial-ai")

        async def subscription_command_reply(update_id: int, at: datetime) -> str:
            async with paid_lifecycle_sessions() as session, session.begin():
                inbox_result = await persist_update(
                    session,
                    bot_id=BOT_ID,
                    payload={
                        "update_id": update_id,
                        "message": {
                            "message_id": update_id,
                            "date": int(at.timestamp()),
                            "chat": {"id": TELEGRAM_USER_ID, "type": "private"},
                            "from": {"id": TELEGRAM_USER_ID, "is_bot": False},
                            "text": "/subscription",
                        },
                    },
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert inbox_result is not None and inbox_result.accepted
            claim = await claim_application_job(
                paid_lifecycle_sessions, worker_id=f"issue26-subscription-{update_id}"
            )
            assert claim is not None and claim.owner_id == owner_id
            await process_application_job(paid_lifecycle_sessions, claim, now=at)
            async with paid_lifecycle_sessions() as session:
                reply = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "telegram.reply.requested",
                        OutboxEvent.causation_id == claim.job_id,
                    )
                )
                assert reply is not None
            reply_payload = reply.payload.get("payload")
            assert isinstance(reply_payload, dict)
            plain_text = reply_payload.get("plain_text")
            assert isinstance(plain_text, str)
            return plain_text

        async def drain_delivery(at: datetime) -> bool:
            processed_any = False
            for _ in range(20):
                processed = await process_delivery_iteration(
                    paid_lifecycle_sessions,
                    transport,
                    worker_id="issue26-paid-local",
                    now=lambda: at,
                )
                if not processed:
                    return processed_any
                processed_any = True
            raise AssertionError("delivery worker did not become idle within the bounded drain")

        # The first persisted private update was queued before access setup; process it now.
        claim = await claim_application_job(
            paid_lifecycle_sessions, worker_id="issue26-subscription-trial"
        )
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(
            paid_lifecycle_sessions, claim, now=base + timedelta(seconds=2)
        )
        async with paid_lifecycle_sessions() as session:
            reply = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.causation_id == claim.job_id,
                )
            )
            assert reply is not None
            reply_payload = reply.payload.get("payload")
            assert isinstance(reply_payload, dict)
            trial_text = reply_payload.get("plain_text")
            assert isinstance(trial_text, str)
        async with paid_lifecycle_sessions() as session:
            trial_status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, base + timedelta(seconds=2))
            )
            assert trial_status.active and trial_status.period_id == trial_period_id
            assert trial_status.ai_turn_limit == PlanPolicy.trial().allowance.ai_turns
            assert trial_status.ai_turns_remaining == 0
            assert "Trial access is active" in trial_text
            assert "AI turns remaining: 0" in trial_text
        async with paid_lifecycle_sessions() as session:
            intents = list(
                await session.scalars(
                    select(ReminderIntentRecord).where(ReminderIntentRecord.owner_id == owner_id)
                )
            )
            assert len(intents) == 4
            reminder_ids = {str(row.plan_item_id): row.id for row in intents}
            trial_intent = next(
                row for row in intents if row.due_at.date() == (base + timedelta(days=1)).date()
            )
            reminder_ids["trial"] = trial_intent.id
            paid_intent = next(
                row for row in intents if row.due_at.date() == (base + timedelta(days=5)).date()
            )
            reminder_ids["paid"] = paid_intent.id
            renewed_intent = next(
                row
                for row in intents
                if row.due_at.date() == (base + timedelta(days=32)).date()
            )
            reminder_ids["renewed"] = renewed_intent.id
            expired_intent = next(
                row for row in intents if row.due_at.date() == (base + timedelta(days=62)).date()
            )
            reminder_ids["expired"] = expired_intent.id

        clock.at = base + timedelta(days=1, hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 1
        async with paid_lifecycle_sessions() as session:
            trial_attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == reminder_ids["trial"]
                )
            )
            assert trial_attempt is not None and trial_attempt.outcome == "confirmed"

        payment_at = base + timedelta(days=1, hours=3)
        receipt = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=TELEGRAM_USER_ID,
            payload=f"issue26-{owner_id}",
            charge_id=f"synthetic-charge-{owner_id}",
            at=payment_at,
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            await payments.view_terms(ViewPaymentTerms(owner_id, payment_at))
            await payments.accept_terms(AcceptPaymentTerms(owner_id, payment_at))
            invoice = await payments.create_order(
                CreateStarsOrder(owner_id, TELEGRAM_USER_ID, receipt.invoice_payload, payment_at)
            )
            approved = await payments.check_checkout(
                CheckStarsCheckout(
                    owner_id,
                    TELEGRAM_USER_ID,
                    invoice.payload,
                    f"synthetic-checkout-{owner_id}",
                    STARS_CURRENCY,
                    STARS_PRICE,
                    payment_at,
                )
            )
            assert approved.ok
            assert await payments.record_payment(receipt)
            assert await payments.record_payment(receipt)
            access = SqlAlchemyAccessUsageStore(session)
            paid_status = await access.read_subscription_status(
                ReadSubscriptionStatus(owner_id, payment_at + timedelta(seconds=1))
            )
            assert paid_status.active and paid_status.period_kind is PeriodKind.PAID
            assert paid_status.ai_turn_limit == PlanPolicy.paid().allowance.ai_turns
            assert paid_status.ai_turns_remaining == PlanPolicy.paid().allowance.ai_turns
            paid_period_id = cast(str, paid_status.period_id)
            assert paid_status.ends_at == payment_at + STARS_PERIOD
            receipts = await session.scalar(
                select(func.count())
                .select_from(PaymentReceiptRecord)
                .where(PaymentReceiptRecord.owner_id == owner_id)
            )
            paid_periods = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert receipts == paid_periods == 1

        paid_quota_time = base + timedelta(days=2)
        await exhaust_ai(paid_quota_time, PlanPolicy.paid().allowance.ai_turns, "paid-ai")
        paid_text = await subscription_command_reply(2, paid_quota_time)
        async with paid_lifecycle_sessions() as session:
            paid_status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_quota_time)
            )
            assert paid_status.active and paid_status.period_id == paid_period_id
            assert paid_status.ai_turns_remaining == 0
            assert "AI turns remaining: 0" in paid_text
            assert "Reminders continue independently" in paid_text

        clock.at = base + timedelta(days=5, hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 2
        async with paid_lifecycle_sessions() as session:
            paid_attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == reminder_ids["paid"]
                )
            )
            assert paid_attempt is not None and paid_attempt.outcome == "confirmed"
            assert paid_attempt.access_period_id == paid_period_id

        paid_end = payment_at + STARS_PERIOD
        renewal = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=TELEGRAM_USER_ID,
            payload=receipt.invoice_payload,
            charge_id=f"synthetic-renewal-{owner_id}",
            at=paid_end,
            is_first_recurring=False,
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            assert await payments.record_payment(renewal)
            assert await payments.record_payment(renewal)
            renewed_status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_end + timedelta(seconds=1))
            )
            assert renewed_status.active and renewed_status.period_kind is PeriodKind.PAID
            assert renewed_status.ends_at == paid_end + STARS_PERIOD
            renewed_period_id = cast(str, renewed_status.period_id)
            receipts = await session.scalar(
                select(func.count())
                .select_from(PaymentReceiptRecord)
                .where(PaymentReceiptRecord.owner_id == owner_id)
            )
            paid_periods = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == "paid",
                )
            )
            assert receipts == paid_periods == 2

        clock.at = paid_end + timedelta(days=1, hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 3
        async with paid_lifecycle_sessions() as session:
            renewed_attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == reminder_ids["renewed"]
                )
            )
            assert renewed_attempt is not None and renewed_attempt.outcome == "confirmed"
            assert renewed_attempt.access_period_id == renewed_period_id

        clock.at = paid_end + STARS_PERIOD + timedelta(days=1, hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 3
        async with paid_lifecycle_sessions() as session:
            expired_attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == reminder_ids["expired"]
                )
            )
            assert expired_attempt is not None
            assert expired_attempt.outcome == "skipped"
            assert expired_attempt.failure_code == "access_expired"
            assert await session.scalar(
                select(func.count())
                .select_from(PaymentReceiptRecord)
                .where(PaymentReceiptRecord.owner_id == owner_id)
            ) == 2

    run_local_async(scenario)


def test_cancel_and_refund_preserve_unrelated_paid_coverage_and_reminders(
    paid_lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    base = datetime(2026, 10, 15, 9, tzinfo=UTC)
    clock = _Clock(base)
    transport = _LocalDeliveryTransport()
    owner_id: UUID
    trial_period_id: str
    renewed_period_id: str
    receipt_a: RecordStarsPayment
    receipt_b: RecordStarsPayment
    order_id: UUID
    plan_ids: set[UUID]
    reminder_ids: set[UUID]

    async def scenario() -> None:
        nonlocal owner_id, trial_period_id, renewed_period_id
        nonlocal receipt_a, receipt_b, order_id, plan_ids, reminder_ids
        async with paid_lifecycle_sessions() as session, session.begin():
            accepted = await persist_update(
                session,
                bot_id=CANCEL_BOT_ID,
                payload={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": int(base.timestamp()),
                        "chat": {"id": CANCEL_TELEGRAM_USER_ID, "type": "private"},
                        "from": {"id": CANCEL_TELEGRAM_USER_ID, "is_bot": False},
                        "text": "/subscription",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert accepted is not None and accepted.accepted
            inbox = await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == CANCEL_BOT_ID,
                    InboxUpdate.update_id == 1,
                )
            )
            assert inbox is not None and inbox.owner_id is not None
            owner_id = inbox.owner_id
            trial = await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, CANCEL_TELEGRAM_USER_ID, base)
            )
            assert trial.status is TrialActivationStatus.ACTIVATED
            trial_period_id = str(trial.period_id)
            preferences = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=0,
                    reminder_elapsed_hours=0,
                ),
            )
            assert preferences is not None

        drafts = tuple(
            OneOffPlanDraft(
                kind=PlanKind.TASK,
                title=title,
                local_date=(base + timedelta(days=event_day)).date(),
                local_time=time(12),
                event_zone="UTC",
                reminders=(ReminderOffset(OffsetKind.ELAPSED_SECONDS, 25 * 60 * 60),),
            )
            for title, event_day in (
                ("Paid through cancellation", 33),
                ("Coverage survives refund", 34),
            )
        )
        proof = ConfirmationProof(uuid4(), 1, f"issue26-cancel-{uuid4().hex}")
        async with paid_lifecycle_sessions() as session, session.begin():
            identity = SqlAlchemyIdentityStore(session)
            profile = await identity.get_preferences(owner_id)
            assert profile is not None
            result = await create_confirmed_plan_batch(
                ConfirmedPlanBatch(f"issue26-cancel-{uuid4().hex}", profile.version, drafts, proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemyPlanningStore(session),
                identity=identity,
                billing=SqlAlchemyAccessUsageStore(session),
                confirmation_verifier=_ConfirmedLocalPreview(proof),
                clock=clock,
            )
            assert len(cast(list[object], result["plans"])) == 2
        assert await process_pending_reminder_schedules(
            paid_lifecycle_sessions, now=base, limit=1000
        ) >= 2

        async def command_reply(
            update_id: int,
            command_text: str,
            at: datetime,
            *,
            already_persisted: bool = False,
        ) -> str:
            if not already_persisted:
                async with paid_lifecycle_sessions() as session, session.begin():
                    accepted = await persist_update(
                        session,
                        bot_id=CANCEL_BOT_ID,
                        payload={
                            "update_id": update_id,
                            "message": {
                                "message_id": update_id,
                                "date": int(at.timestamp()),
                                "chat": {"id": CANCEL_TELEGRAM_USER_ID, "type": "private"},
                                "from": {"id": CANCEL_TELEGRAM_USER_ID, "is_bot": False},
                                "text": command_text,
                            },
                        },
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert accepted is not None and accepted.accepted
            claim = await claim_application_job(
                paid_lifecycle_sessions, worker_id=f"issue26-cancel-app-{update_id}"
            )
            assert claim is not None and claim.owner_id == owner_id
            await process_application_job(paid_lifecycle_sessions, claim, now=at)
            async with paid_lifecycle_sessions() as session:
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "telegram.reply.requested",
                        OutboxEvent.causation_id == claim.job_id,
                    )
                )
                assert event is not None
                payload = event.payload.get("payload")
                assert isinstance(payload, dict)
                reply = payload.get("plain_text")
                assert isinstance(reply, str)
                return reply

        async def drain_delivery(at: datetime) -> bool:
            processed_any = False
            for _ in range(20):
                processed = await process_delivery_iteration(
                    paid_lifecycle_sessions,
                    transport,
                    worker_id="issue26-cancel-refund-local",
                    now=lambda: at,
                )
                if not processed:
                    return processed_any
                processed_any = True
            raise AssertionError("delivery worker did not become idle within the bounded drain")

        trial_reply = await command_reply(
            1, "/subscription", base + timedelta(seconds=1), already_persisted=True
        )
        assert "Trial access is active" in trial_reply
        async with paid_lifecycle_sessions() as session:
            trial_status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, base + timedelta(seconds=1))
            )
            assert trial_status.active and trial_status.period_id == trial_period_id

        payment_at = base + timedelta(days=1, hours=3)
        invoice_payload = f"issue26-cancel-{owner_id}"
        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            await payments.view_terms(ViewPaymentTerms(owner_id, payment_at))
            await payments.accept_terms(AcceptPaymentTerms(owner_id, payment_at))
            invoice = await payments.create_order(
                CreateStarsOrder(
                    owner_id, CANCEL_TELEGRAM_USER_ID, invoice_payload, payment_at
                )
            )
        assert await drain_delivery(payment_at)
        assert len(transport.invoices) == 1
        assert transport.invoices[0] == (CANCEL_TELEGRAM_USER_ID, invoice_payload, "en")
        receipt_a = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=CANCEL_TELEGRAM_USER_ID,
            payload=invoice.payload,
            charge_id=f"synthetic-cancel-first-{owner_id}",
            at=payment_at,
        )
        paid_end_a = payment_at + STARS_PERIOD
        receipt_b = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=CANCEL_TELEGRAM_USER_ID,
            payload=invoice.payload,
            charge_id=f"synthetic-cancel-renewal-{owner_id}",
            at=paid_end_a,
            is_first_recurring=False,
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            order = await session.scalar(
                select(PaymentOrderRecord).where(
                    PaymentOrderRecord.invoice_payload == invoice_payload
                )
            )
            assert order is not None
            order_id = order.id
            assert (
                await payments.check_checkout(
                    CheckStarsCheckout(
                        owner_id,
                        CANCEL_TELEGRAM_USER_ID,
                        invoice_payload,
                        f"synthetic-cancel-checkout-{owner_id}",
                        STARS_CURRENCY,
                        STARS_PRICE,
                        payment_at,
                    )
                )
            ).ok
            assert await payments.record_payment(receipt_a)
        async with paid_lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyStarsPaymentStore(session).record_payment(receipt_b)
            renewed = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_end_a + timedelta(seconds=1))
            )
            assert renewed.active and renewed.period_kind is PeriodKind.PAID
            renewed_period_id = cast(str, renewed.period_id)
            assert renewed.ends_at == paid_end_a + STARS_PERIOD

        cancel_at = paid_end_a + timedelta(hours=12)
        cancel_reply = await command_reply(2, "/cancel", cancel_at)
        assert "renewal cancellation was queued" in cancel_reply
        async with paid_lifecycle_sessions() as session:
            order = await session.get(PaymentOrderRecord, order_id)
            assert order is not None and order.cancel_status == "queued"
            assert order.latest_period_end == paid_end_a + STARS_PERIOD
        assert await drain_delivery(cancel_at)
        assert transport.cancelled == [(CANCEL_TELEGRAM_USER_ID, receipt_a.telegram_charge_id)]
        async with paid_lifecycle_sessions() as session:
            order = await session.get(PaymentOrderRecord, order_id)
            assert order is not None
            assert order.cancel_status == "confirmed"
            assert order.renewal_cancelled_at == cancel_at
            assert order.latest_period_end == paid_end_a + STARS_PERIOD
            assert (
                await SqlAlchemyStarsPaymentStore(session).renewal_status(owner_id)
                == "cancelled"
            )

        subscription_after_cancel = await command_reply(
            3, "/subscription", cancel_at + timedelta(seconds=1)
        )
        assert "Trial access is active" not in subscription_after_cancel
        assert "Auto-renewal is disabled" in subscription_after_cancel
        assert "AI turns remaining: 1000" in subscription_after_cancel

        reminder_at_a = paid_end_a + timedelta(days=1)
        clock.at = reminder_at_a
        assert await drain_delivery(clock.now())
        async with paid_lifecycle_sessions() as session:
            intents = list(
                await session.scalars(
                    select(ReminderIntentRecord)
                    .where(ReminderIntentRecord.owner_id == owner_id)
                    .order_by(ReminderIntentRecord.due_at)
                )
            )
            attempts = list(
                await session.scalars(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id.in_(
                            select(ReminderIntentRecord.id).where(
                                ReminderIntentRecord.owner_id == owner_id
                            )
                        )
                    )
                )
            )
            assert len(intents) == 2
            assert len(attempts) == 1 and attempts[0].outcome == "confirmed"
            assert attempts[0].access_period_id == renewed_period_id
            reminder_ids = {intent.id for intent in intents}
            plan_ids = set(
                await session.scalars(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
                )
            )
            assert len(plan_ids) == 2

        quota_at = reminder_at_a + timedelta(hours=1)
        async with paid_lifecycle_sessions() as session, session.begin():
            billing = SqlAlchemyAccessUsageStore(session)
            reserved = await billing.reserve_usage(
                ReserveUsage(
                    owner_id,
                    f"issue26-refund-quota-{owner_id}",
                    Operation.AI_TURN,
                    1,
                    0,
                    quota_at,
                    quota_at + timedelta(minutes=5),
                )
            )
            assert reserved.access.allowed and reserved.reservation is not None
            await billing.settle_usage(
                owner_id=owner_id,
                request_id=f"issue26-refund-quota-{owner_id}",
                ai_turns=1,
                voice_seconds=0,
                at=quota_at,
            )

        async with paid_lifecycle_sessions() as session, session.begin():
            user_id, charge_id = await SqlAlchemyStarsPaymentStore(session).begin_refund(
                RequestStarsRefund(receipt_a.telegram_charge_id, "issue26-local-operator", quota_at)
            )
            assert user_id == CANCEL_TELEGRAM_USER_ID
            assert charge_id == receipt_a.telegram_charge_id
        async with paid_lifecycle_sessions() as session:
            refund = await session.get(PaymentRefundRecord, receipt_a.telegram_charge_id)
            assert refund is not None and refund.status == "requested"
        async with paid_lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyStarsPaymentStore(session).finish_refund(
                FinishStarsRefund(receipt_a.telegram_charge_id, "confirmed", quota_at)
            )

        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            assert await payments.record_payment(receipt_a)
            status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, quota_at + timedelta(seconds=1))
            )
            assert status.active and status.period_id == renewed_period_id
            assert status.ai_turns_remaining == PlanPolicy.paid().allowance.ai_turns - 1
            periods = list(
                await session.scalars(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == "paid",
                    )
                )
            )
            assert len(periods) == 1 and periods[0].period_id == renewed_period_id
            refunded = await session.get(PaymentReceiptRecord, receipt_a.telegram_charge_id)
            assert refunded is not None and refunded.refunded_at == quota_at
            refund = await session.get(PaymentRefundRecord, receipt_a.telegram_charge_id)
            assert refund is not None and refund.status == "confirmed"
            unrefunded = await session.get(PaymentReceiptRecord, receipt_b.telegram_charge_id)
            assert unrefunded is not None and unrefunded.refunded_at is None
            assert await session.scalar(
                select(func.count())
                .select_from(PaymentReceiptRecord)
                .where(PaymentReceiptRecord.owner_id == owner_id)
            ) == 2
            remaining_plan_ids = set(
                await session.scalars(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
                )
            )
            remaining_reminder_ids = set(
                await session.scalars(
                    select(ReminderIntentRecord.id).where(
                        ReminderIntentRecord.owner_id == owner_id
                    )
                )
            )
            assert remaining_plan_ids == plan_ids
            assert remaining_reminder_ids == reminder_ids

        subscription_after_refund = await command_reply(
            4, "/subscription", quota_at + timedelta(seconds=2)
        )
        assert "Auto-renewal is disabled" in subscription_after_refund
        assert "AI turns remaining: 999" in subscription_after_refund

        reminder_at_b = paid_end_a + timedelta(days=2, hours=1)
        clock.at = reminder_at_b
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 2
        async with paid_lifecycle_sessions() as session:
            final_attempts = list(
                await session.scalars(
                    select(DeliveryAttemptRecord)
                    .join(
                        ReminderIntentRecord,
                        ReminderIntentRecord.id == DeliveryAttemptRecord.intent_id,
                    )
                    .where(ReminderIntentRecord.owner_id == owner_id)
                )
            )
            assert len(final_attempts) == 2
            assert all(row.outcome == "confirmed" for row in final_attempts)
            assert {row.access_period_id for row in final_attempts} == {renewed_period_id}
            final_status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, reminder_at_b)
            )
            assert final_status.active and final_status.period_id == renewed_period_id
            assert final_status.ai_turns_remaining == PlanPolicy.paid().allowance.ai_turns - 1

    run_local_async(scenario)
