from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Iterator
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

from pin.adapters.retention.deletion import finish_pending_deletions
from pin.adapters.telegram.access_notices import schedule_access_notices
from pin.adapters.telegram.account_deletion import (
    apply_account_deletion_callback,
    begin_account_deletion,
    current_deletion_preview_version,
)
from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
    process_pending_reminder_schedules,
)
from pin.adapters.telegram.deletion import purge_account
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
    PaymentRejected,
    ReadSubscriptionStatus,
    RecordStarsPayment,
    RequestStarsRefund,
    ReserveUsage,
    RestoreReturningPaidAccess,
    RetainBillingIdentity,
    SqlAlchemyAccessUsageStore,
    SqlAlchemyStarsPaymentStore,
    StartTrial,
    TrialActivationStatus,
    ViewPaymentTerms,
)
from pin.contexts.billing.domain.access import InvalidTransition, PeriodKind, PlanPolicy
from pin.contexts.billing.domain.payments import STARS_CURRENCY, STARS_PERIOD, STARS_PRICE
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessExpiryNoticeRecord,
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
    RetainedBillingIdentityRecord,
    UsageReservationRecord,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import (
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.infrastructure.delivery_worker import enqueue_due_reply_jobs
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
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue26_paid_"
DATABASE_USER = "pin_test"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
BOT_ID = 8_100_026
TELEGRAM_USER_ID = 8_200_026
CANCEL_BOT_ID = 8_100_027
CANCEL_TELEGRAM_USER_ID = 8_200_027
EXPIRY_BOT_ID = 8_100_029
EXPIRY_TELEGRAM_USER_ID = 8_200_029


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
        self.reply_texts: list[str] = []
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

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.replies += 1
        self.reply_texts.append(message.plain_text)
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

    ru_terms = _landing_value("ru", "termsTrial")
    assert "семь дней" in ru_terms
    assert f"{trial.allowance.ai_turns} AI-взаимодействий" in ru_terms
    assert f"{trial.allowance.voice_seconds // 60} минут" in ru_terms
    assert f"{STARS_PRICE} Stars за {paid.duration.days} дней" in ru_terms
    assert f"{paid.allowance.ai_turns:,}".replace(",", " ") in ru_terms
    assert f"{paid.allowance.voice_seconds // 60} минут" in ru_terms


def test_trial_warning_quiet_hours_expiry_and_saved_plan_are_composed(
    paid_lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _assert_landing_matches_billing()
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(days=8)
    clock = _Clock(base)
    transport = _LocalDeliveryTransport()
    owner_id: UUID
    trial_end: datetime
    reminder_id: UUID

    async def scenario() -> None:
        nonlocal owner_id, trial_end, reminder_id
        async with paid_lifecycle_sessions() as session, session.begin():
            owner_id = await PostgresTelegramOwnerResolver().resolve(
                session, bot_id=EXPIRY_BOT_ID, telegram_user_id=EXPIRY_TELEGRAM_USER_ID
            )
            trial = await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, EXPIRY_TELEGRAM_USER_ID, base)
            )
            assert trial.status is TrialActivationStatus.ACTIVATED
            assert trial.period_id is not None
            trial_end = base + PlanPolicy.trial().duration
            warning_at = trial_end - timedelta(hours=24)
            quiet_start = (warning_at - timedelta(minutes=30)).time().replace(tzinfo=None)
            quiet_end = (warning_at + timedelta(minutes=90)).time().replace(tzinfo=None)
            profile = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=0,
                    reminder_elapsed_hours=0,
                    quiet_start=quiet_start,
                    quiet_end=quiet_end,
                ),
            )
            assert profile is not None

        proof = ConfirmationProof(uuid4(), 1, f"issue26-expiry-{uuid4().hex}")
        event_at = trial_end + timedelta(hours=24)
        draft = OneOffPlanDraft(
            kind=PlanKind.TASK,
            title="Retained after trial expiry",
            local_date=event_at.date(),
            local_time=event_at.time().replace(tzinfo=None),
            event_zone="UTC",
            reminders=(ReminderOffset(OffsetKind.ELAPSED_SECONDS, 23 * 60 * 60),),
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            identity = SqlAlchemyIdentityStore(session)
            profile = await identity.get_preferences(owner_id)
            assert profile is not None
            saved = await create_confirmed_plan_batch(
                ConfirmedPlanBatch("issue26-expiry-plan", profile.version, (draft,), proof),
                authenticated_owner_id=owner_id,
                store=SqlAlchemyPlanningStore(session),
                identity=identity,
                billing=SqlAlchemyAccessUsageStore(session),
                confirmation_verifier=_ConfirmedLocalPreview(proof),
                clock=clock,
            )
            assert len(cast(list[object], saved["plans"])) == 1
        assert await process_pending_reminder_schedules(
            paid_lifecycle_sessions, now=base, limit=100
        ) == 1
        async with paid_lifecycle_sessions() as session:
            intent = await session.scalar(
                select(ReminderIntentRecord).where(ReminderIntentRecord.owner_id == owner_id)
            )
            assert intent is not None
            reminder_id = intent.id
            assert intent.due_at == trial_end + timedelta(hours=1)

        async def drain_delivery(at: datetime) -> bool:
            processed_any = False
            for _ in range(20):
                processed = await process_delivery_iteration(
                    paid_lifecycle_sessions,
                    transport,
                    worker_id="issue26-expiry-local",
                    now=lambda: at,
                )
                if not processed:
                    return processed_any
                processed_any = True
            raise AssertionError("delivery worker did not become idle within the bounded drain")

        assert await drain_delivery(base)
        assert transport.sent == [] and transport.replies == 0
        async with paid_lifecycle_sessions() as session, session.begin():
            assert await schedule_access_notices(session, transport, at=warning_at) == 1
            notice_reply = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.owner_id == owner_id,
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.causation_id.is_not(None),
                ).order_by(OutboxEvent.created_at.desc())
            )
            assert notice_reply is not None
            # The fixture injects historical application time; align this generated event's
            # DB-default timestamp so the real job queue sees the same frozen timeline.
            notice_reply.created_at = warning_at
            assert await enqueue_due_reply_jobs(session, now=warning_at) == 1
        assert await process_delivery_iteration(
            paid_lifecycle_sessions,
            transport,
            worker_id="issue26-expiry-local",
            now=lambda: warning_at,
        )
        assert transport.replies == 0  # The 24-hour notice is deferred during quiet hours.
        async with paid_lifecycle_sessions() as session, session.begin():
            billing = SqlAlchemyAccessUsageStore(session)
            assert await billing.enqueue_due_access_notices(at=warning_at) == 0
            notices = list(
                await session.scalars(
                    select(AccessExpiryNoticeRecord).where(
                        AccessExpiryNoticeRecord.owner_id == owner_id
                    )
                )
            )
            assert len(notices) == 1 and notices[0].kind == "expiry_24h"
            replies = list(
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.owner_id == owner_id,
                        OutboxEvent.event_type == "telegram.reply.requested",
                    )
                )
            )
            notice_reply = next(
                event
                for event in replies
                if isinstance(event.payload.get("payload"), dict)
                and event.payload["payload"].get("billing_access_notice") is not None
            )
            jobs = list(
                await session.scalars(select(Job).where(Job.owner_id == owner_id))
            )
            deferred = next(
                job
                for job in jobs
                if job.payload.get("reply_event_id") == str(notice_reply.id)
            )
            db_now = await session.scalar(select(func.clock_timestamp()))
            assert db_now is not None
            assert deferred.status == "pending" and deferred.last_error == "quiet_hours"
            assert timedelta(minutes=59) <= deferred.available_at - db_now <= timedelta(hours=1)
            # The worker retry uses PostgreSQL's live clock; project that verified retry
            # onto this historical fixture timeline before testing the quiet-end delivery.
            deferred.available_at = warning_at + timedelta(hours=1)

        clock.at = warning_at + timedelta(hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.reply_texts) == 1
        assert "After expiry" in transport.reply_texts[0]

        one_hour_warning = trial_end - timedelta(hours=1)
        clock.at = one_hour_warning
        async with paid_lifecycle_sessions() as session, session.begin():
            assert await schedule_access_notices(
                session, transport, at=one_hour_warning
            ) == 1
            replies = list(
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.owner_id == owner_id,
                        OutboxEvent.event_type == "telegram.reply.requested",
                    )
                )
            )
            notices = list(
                await session.scalars(
                    select(AccessExpiryNoticeRecord).where(
                        AccessExpiryNoticeRecord.owner_id == owner_id
                    )
                )
            )
            one_hour_notice = next(notice for notice in notices if notice.kind == "expiry_1h")
            one_hour_reply = next(
                event
                for event in replies
                if isinstance(event.payload.get("payload"), dict)
                and event.payload["payload"].get("billing_access_notice") is not None
                and event.payload["payload"]["billing_access_notice"].get("notice_id")
                == str(one_hour_notice.id)
            )
            one_hour_reply.created_at = one_hour_warning
            assert await enqueue_due_reply_jobs(session, now=one_hour_warning) == 1
        assert await drain_delivery(clock.now())
        async with paid_lifecycle_sessions() as session:
            notices = list(
                await session.scalars(
                    select(AccessExpiryNoticeRecord)
                    .where(AccessExpiryNoticeRecord.owner_id == owner_id)
                    .order_by(AccessExpiryNoticeRecord.kind)
                )
            )
            assert {notice.kind for notice in notices} == {"expiry_1h", "expiry_24h"}
            assert len(notices) == 2
        assert len(transport.reply_texts) == 2

        async with paid_lifecycle_sessions() as session, session.begin():
            result = await persist_update(
                session,
                bot_id=EXPIRY_BOT_ID,
                payload={
                    "update_id": 21,
                    "message": {
                        "message_id": 21,
                        "date": int((trial_end + timedelta(hours=2)).timestamp()),
                        "chat": {"id": EXPIRY_TELEGRAM_USER_ID, "type": "private"},
                        "from": {"id": EXPIRY_TELEGRAM_USER_ID, "is_bot": False},
                        "text": "/subscription",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert result is not None and result.accepted
        claim = await claim_application_job(
            paid_lifecycle_sessions, worker_id="issue26-expiry-subscription"
        )
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(
            paid_lifecycle_sessions, claim, now=trial_end + timedelta(hours=2)
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            reply = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.causation_id == claim.job_id,
                )
            )
            assert reply is not None
            payload = reply.payload.get("payload")
            assert isinstance(payload, dict)
            plain_text = payload.get("plain_text")
            assert isinstance(plain_text, str)
            assert "There is no active access." in plain_text
            assert "You can still view, complete and cancel plans." in plain_text
            reply.created_at = trial_end + timedelta(hours=2)
            assert await enqueue_due_reply_jobs(
                session, now=trial_end + timedelta(hours=2)
            ) == 1
            status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, trial_end + timedelta(hours=2))
            )
            assert not status.active and status.trial_used
            plans = list(
                await session.scalars(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
            )
            assert len(plans) == 1

        clock.at = trial_end + timedelta(hours=2)
        assert await drain_delivery(clock.now())
        assert len(transport.sent) == 0
        async with paid_lifecycle_sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == reminder_id
                )
            )
            assert attempt is not None
            assert attempt.outcome == "skipped"
            assert attempt.failure_code == "access_expired"
            plans = list(
                await session.scalars(
                    select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                )
            )
            assert len(plans) == 1
        assert len(transport.reply_texts) == 3
        assert "There is no active access." in transport.reply_texts[-1]

    run_local_async(scenario)


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


def test_paid_period_boundary_and_deletion_fence_race(
    paid_lifecycle_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    base = datetime(2026, 10, 15, 9, tzinfo=UTC)
    clock = _Clock(base)
    owner_bot_id = 8_100_028
    telegram_user_id = 8_200_028
    owner_id: UUID
    first_receipt: RecordStarsPayment
    renewal_receipt: RecordStarsPayment
    first_period_id: str
    renewed_period_id: str
    intent_at_end: UUID
    intent_after_end: UUID

    class _BoundaryTransport(_LocalDeliveryTransport):
        def __init__(self) -> None:
            super().__init__()
            self.unknown_next = True
            self.pause_next = False
            self.send_started = asyncio.Event()
            self.allow_send_to_finish = asyncio.Event()
            self.invocations: list[tuple[int, str, datetime]] = []
            self.paused_at: datetime | None = None

        async def send_reminder(self, message: ReminderSend) -> str:
            attempt_id = f"issue26-boundary-send-{len(self.invocations) + 1}"
            self.invocations.append((message.destination_id, attempt_id, clock.now()))
            if self.unknown_next:
                self.unknown_next = False
                raise UnknownDeliveryFailure("synthetic_outcome_unknown")
            if self.pause_next:
                self.pause_next = False
                self.paused_at = clock.now()
                self.send_started.set()
                await self.allow_send_to_finish.wait()
            return attempt_id

    local_transport = _BoundaryTransport()

    async def scenario() -> None:
        nonlocal owner_id, first_receipt, renewal_receipt
        nonlocal first_period_id, renewed_period_id, intent_at_end, intent_after_end
        async with paid_lifecycle_sessions() as session, session.begin():
            accepted = await persist_update(
                session,
                bot_id=owner_bot_id,
                payload={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": int(base.timestamp()),
                        "chat": {"id": telegram_user_id, "type": "private"},
                        "from": {"id": telegram_user_id, "is_bot": False},
                        "text": "/subscription",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert accepted is not None and accepted.accepted
            inbox = await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == owner_bot_id,
                    InboxUpdate.update_id == 1,
                )
            )
            assert inbox is not None and inbox.owner_id is not None
            owner_id = inbox.owner_id
            trial = await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, telegram_user_id, base)
            )
            assert trial.status is TrialActivationStatus.ACTIVATED
            saved_preferences = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=0,
                    reminder_elapsed_hours=0,
                ),
            )
            assert saved_preferences is not None

        payment_at = base + timedelta(seconds=1)
        invoice_payload = f"issue26-boundary-{owner_id}"
        first_receipt = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=telegram_user_id,
            payload=invoice_payload,
            charge_id=f"synthetic-boundary-first-{owner_id}",
            at=payment_at,
        )
        paid_end = payment_at + STARS_PERIOD
        renewal_receipt = _synthetic_receipt(
            owner_id=owner_id,
            telegram_user_id=telegram_user_id,
            payload=invoice_payload,
            charge_id=f"synthetic-boundary-renewal-{owner_id}",
            at=paid_end,
            is_first_recurring=False,
        )
        async with paid_lifecycle_sessions() as session, session.begin():
            payments = SqlAlchemyStarsPaymentStore(session)
            await payments.view_terms(ViewPaymentTerms(owner_id, payment_at))
            await payments.accept_terms(AcceptPaymentTerms(owner_id, payment_at))
            order = await payments.create_order(
                CreateStarsOrder(owner_id, telegram_user_id, invoice_payload, payment_at)
            )
            assert (
                await payments.check_checkout(
                    CheckStarsCheckout(
                        owner_id,
                        telegram_user_id,
                        order.payload,
                        f"synthetic-boundary-checkout-{owner_id}",
                        STARS_CURRENCY,
                        STARS_PRICE,
                        payment_at,
                    )
                )
            ).ok
            assert await payments.record_payment(first_receipt)
            status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, payment_at)
            )
            assert status.active and status.period_kind is PeriodKind.PAID
            first_period_id = cast(str, status.period_id)
            assert status.ends_at == paid_end
            assert await SqlAlchemyAccessUsageStore(session).retain_owner_for_return(
                RetainBillingIdentity(owner_id, telegram_user_id, payment_at)
            )

        event_start = paid_end + timedelta(days=2)
        proof = ConfirmationProof(uuid4(), 1, f"issue26-boundary-plan-{uuid4().hex}")
        async with paid_lifecycle_sessions() as session, session.begin():
            identity = SqlAlchemyIdentityStore(session)
            profile = await identity.get_preferences(owner_id)
            assert profile is not None
            await create_confirmed_plan_batch(
                ConfirmedPlanBatch(
                    f"issue26-boundary-{owner_id}",
                    profile.version,
                    (
                        OneOffPlanDraft(
                            kind=PlanKind.TASK,
                            title="Boundary reminder survives until deletion",
                            local_date=event_start.date(),
                            local_time=time(
                                event_start.hour, event_start.minute, event_start.second
                            ),
                            event_zone="UTC",
                            reminders=(
                                ReminderOffset(OffsetKind.ELAPSED_SECONDS, 48 * 60 * 60),
                                ReminderOffset(OffsetKind.ELAPSED_SECONDS, 47 * 60 * 60),
                            ),
                        ),
                    ),
                    proof,
                ),
                authenticated_owner_id=owner_id,
                store=SqlAlchemyPlanningStore(session),
                identity=identity,
                billing=SqlAlchemyAccessUsageStore(session),
                confirmation_verifier=_ConfirmedLocalPreview(proof),
                clock=clock,
            )
        assert await process_pending_reminder_schedules(
            paid_lifecycle_sessions, now=base, limit=100
        ) >= 1
        async with paid_lifecycle_sessions() as session:
            intents = list(
                await session.scalars(
                    select(ReminderIntentRecord)
                    .where(ReminderIntentRecord.owner_id == owner_id)
                    .order_by(ReminderIntentRecord.due_at)
                )
            )
            assert len(intents) == 2
            assert [intent.due_at for intent in intents] == [
                paid_end,
                paid_end + timedelta(hours=1),
            ]
            intent_at_end, intent_after_end = intents[0].id, intents[1].id

        held_request_id = f"issue26-boundary-held-{owner_id}"
        async with paid_lifecycle_sessions() as session, session.begin():
            billing = SqlAlchemyAccessUsageStore(session)
            held = await billing.reserve_usage(
                ReserveUsage(
                    owner_id,
                    held_request_id,
                    Operation.AI_TURN,
                    1,
                    0,
                    paid_end - timedelta(seconds=30),
                    paid_end,
                )
            )
            assert held.access.allowed and held.reservation is not None
            assert held.reservation.period_id == first_period_id

        clock.at = paid_end
        boundary_unknown = False
        gate = asyncio.Event()
        ready = 0
        all_ready = asyncio.Event()

        async def at_boundary(
            operation: Callable[[], Awaitable[bool | str]],
        ) -> bool | str:
            nonlocal ready
            ready += 1
            if ready == 3:
                all_ready.set()
            await gate.wait()
            return await operation()

        async def accept_renewal() -> bool:
            async with paid_lifecycle_sessions() as session, session.begin():
                return await SqlAlchemyStarsPaymentStore(session).record_payment(renewal_receipt)

        async def settle_old_period() -> str:
            try:
                async with paid_lifecycle_sessions() as session, session.begin():
                    await SqlAlchemyAccessUsageStore(session).settle_usage(
                        owner_id=owner_id,
                        request_id=held_request_id,
                        ai_turns=1,
                        voice_seconds=0,
                        at=paid_end,
                    )
            except InvalidTransition:
                return "expired_at_period_end"
            return "settled"

        async def deliver_due_reminder() -> bool:
            processed_any = False
            for _ in range(10):
                processed = await process_delivery_iteration(
                    paid_lifecycle_sessions,
                    local_transport,
                    worker_id="issue26-boundary-race",
                    now=clock.now,
                )
                if not processed:
                    return processed_any
                processed_any = True
            raise AssertionError("boundary delivery worker did not become idle")

        boundary_tasks = [
            asyncio.create_task(at_boundary(accept_renewal)),
            asyncio.create_task(at_boundary(settle_old_period)),
            asyncio.create_task(at_boundary(deliver_due_reminder)),
        ]
        await all_ready.wait()
        gate.set()
        renewal_accepted, settlement_outcome, reminder_processed = await asyncio.gather(
            *boundary_tasks
        )
        assert renewal_accepted is True
        assert settlement_outcome == "expired_at_period_end"
        assert isinstance(reminder_processed, bool)

        async with paid_lifecycle_sessions() as session, session.begin():
            billing = SqlAlchemyAccessUsageStore(session)
            assert await billing.expire_reservations(at=paid_end, limit=100) == 1
            old_hold = await billing.get_usage_reservation(owner_id, held_request_id)
            assert old_hold is not None
            assert old_hold.period_id == first_period_id and old_hold.state.value == "expired"
            payments = SqlAlchemyStarsPaymentStore(session)
            assert await payments.record_payment(renewal_receipt)
            status = await billing.read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_end)
            )
            assert status.active and status.period_kind is PeriodKind.PAID
            renewed_period_id = cast(str, status.period_id)
            assert renewed_period_id != first_period_id
            paid_periods = list(
                await session.scalars(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                    )
                )
            )
            assert len(paid_periods) == 2
            assert sum(row.ai_turn_limit for row in paid_periods) == (
                2 * PlanPolicy.paid().allowance.ai_turns
            )
            assert not any(
                row.request_id == held_request_id and row.period_id == renewed_period_id
                for row in await session.scalars(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id
                    )
                )
            )
            boundary_attempts = list(
                await session.scalars(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == intent_at_end
                    )
                )
            )
            assert len(boundary_attempts) <= 1
            if boundary_attempts:
                outcome = boundary_attempts[0].outcome
                assert outcome in {"skipped", "unknown", "coalesced"}
                if outcome == "skipped":
                    assert boundary_attempts[0].failure_code == "access_expired"
                    assert not local_transport.invocations
                elif outcome == "unknown":
                    boundary_unknown = True
                    assert boundary_attempts[0].access_period_id == renewed_period_id
                    assert boundary_attempts[0].failure_code == "synthetic_outcome_unknown"
                else:
                    assert boundary_attempts[0].failure_code == "newer_due_reminder_selected"
                    assert not local_transport.invocations
            else:
                assert not local_transport.invocations

        delete_at = paid_end + timedelta(hours=1, seconds=1)
        clock.at = delete_at - timedelta(seconds=1)
        local_transport.unknown_next = False
        local_transport.pause_next = True
        delivery_task = asyncio.create_task(deliver_due_reminder())
        await asyncio.wait_for(local_transport.send_started.wait(), timeout=15)
        assert local_transport.paused_at == delete_at - timedelta(seconds=1)

        async with paid_lifecycle_sessions() as session, session.begin():
            preview_reply = await begin_account_deletion(
                session, owner_id=owner_id, language="en", now=delete_at
            )
            assert preview_reply.buttons
            confirm_button = next(
                button for button in preview_reply.buttons if button.action == "deletion.confirm"
            )
            expected_version = await current_deletion_preview_version(
                session, owner_id=owner_id, now=delete_at
            )
            result = await apply_account_deletion_callback(
                session,
                owner_id=owner_id,
                action=confirm_button.action,
                preview_id=confirm_button.value,
                expected_version=expected_version,
                language="en",
                now=delete_at,
            )
            assert "Deletion is confirmed" in result.plain_text
        async with paid_lifecycle_sessions() as session:
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None and account.deleted_at == delete_at
            assert account.deletion_pending
        clock.at = delete_at + timedelta(seconds=2)
        local_transport.allow_send_to_finish.set()
        await asyncio.wait_for(delivery_task, timeout=15)

        async with paid_lifecycle_sessions() as session:
            last_reminder_attempt = await session.scalar(
                select(DeliveryAttemptRecord).where(
                    DeliveryAttemptRecord.intent_id == intent_after_end
                )
            )
            assert last_reminder_attempt is not None
            assert last_reminder_attempt.outcome == "confirmed"
            assert last_reminder_attempt.access_period_id == renewed_period_id
            assert local_transport.paused_at is not None
            assert last_reminder_attempt.started_at is not None
            assert last_reminder_attempt.started_at == local_transport.paused_at
            assert last_reminder_attempt.finished_at is not None
            assert last_reminder_attempt.finished_at > delete_at
            assert len(local_transport.invocations) == 1 + int(boundary_unknown)

        artifact = tmp_path / "issue26-paid-deletion-tombstones.jsonl"
        assert (
            await finish_pending_deletions(
                paid_lifecycle_sessions,
                artifact=artifact,
                now=delete_at + timedelta(seconds=5),
                purge_owner=purge_account,
            )
            == 1
        )
        async with paid_lifecycle_sessions() as session:
            assert await SqlAlchemyIdentityStore(session).get_preferences(owner_id) is None
            assert await session.scalar(
                select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
            ) is None
            assert await session.scalar(
                select(ReminderIntentRecord.id).where(ReminderIntentRecord.owner_id == owner_id)
            ) is None
            assert await session.scalar(
                select(TelegramOwnerRecord.owner_id).where(
                    TelegramOwnerRecord.owner_id == owner_id
                )
            ) is None
            assert await session.scalar(
                select(InboxUpdate.id).where(InboxUpdate.owner_id == owner_id)
            ) is None
            assert await session.scalar(
                select(Job.id).where(Job.owner_id == owner_id)
            ) is None
            assert await session.scalar(
                select(OutboxEvent.id).where(OutboxEvent.owner_id == owner_id)
            ) is None
            assert await session.scalar(
                select(func.count())
                .select_from(UsageReservationRecord)
                .where(UsageReservationRecord.owner_id == owner_id)
            ) == 0
            assert await session.get(PaymentReceiptRecord, first_receipt.telegram_charge_id)
            assert await session.get(PaymentReceiptRecord, renewal_receipt.telegram_charge_id)
            assert await session.get(RetainedBillingIdentityRecord, owner_id) is not None
            assert await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.period_id == renewed_period_id,
                )
            ) == 1

        calls_before_post_delete_drain = len(local_transport.invocations)
        clock.at = delete_at + timedelta(minutes=1)
        assert await process_pending_reminder_schedules(
            paid_lifecycle_sessions, now=clock.now(), limit=100
        ) == 0
        assert not await process_delivery_iteration(
            paid_lifecycle_sessions,
            local_transport,
            worker_id="issue26-boundary-after-delete",
            now=clock.now,
        )
        assert len(local_transport.invocations) == calls_before_post_delete_drain

        refund_at = delete_at + timedelta(minutes=2)
        async with paid_lifecycle_sessions() as session, session.begin():
            refund_user, refunded_charge = await SqlAlchemyStarsPaymentStore(session).begin_refund(
                RequestStarsRefund(
                    renewal_receipt.telegram_charge_id,
                    "issue26-local-operator",
                    refund_at,
                )
            )
            assert refund_user == telegram_user_id
            assert refunded_charge == renewal_receipt.telegram_charge_id
        async with paid_lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyStarsPaymentStore(session).finish_refund(
                FinishStarsRefund(renewal_receipt.telegram_charge_id, "confirmed", refund_at)
            )
        try:
            async with paid_lifecycle_sessions() as session, session.begin():
                await SqlAlchemyStarsPaymentStore(session).record_payment(renewal_receipt)
        except PaymentRejected:
            pass
        else:
            raise AssertionError("a post-deletion receipt replay must not recreate paid access")

        async with paid_lifecycle_sessions() as session, session.begin():
            billing = SqlAlchemyAccessUsageStore(session)
            assert not await billing.restore_returning_paid_access(
                RestoreReturningPaidAccess(owner_id, telegram_user_id, refund_at)
            )
            status = await billing.read_subscription_status(
                ReadSubscriptionStatus(owner_id, refund_at)
            )
            assert not status.active
            assert await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(
                    AccessPeriodRecord.owner_id == owner_id,
                    AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                )
            ) == 0
            refunded = await session.get(PaymentReceiptRecord, renewal_receipt.telegram_charge_id)
            refund = await session.get(PaymentRefundRecord, renewal_receipt.telegram_charge_id)
            order = await session.scalar(
                select(PaymentOrderRecord).where(
                    PaymentOrderRecord.invoice_payload == renewal_receipt.invoice_payload
                )
            )
            assert refunded is not None and refunded.refunded_at == refund_at
            assert refund is not None and refund.status == "confirmed"
            assert order is not None
            assert await session.scalar(
                select(func.count())
                .select_from(PaymentReceiptRecord)
                .where(PaymentReceiptRecord.owner_id == owner_id)
            ) == 2
        assert len(local_transport.invocations) == calls_before_post_delete_drain

    run_local_async(scenario)
