from __future__ import annotations

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.billing.api import (
    AccessNoticeKind,
    AccessNoticeStatus,
    ActivatePaidPeriod,
    CheckAccessNotice,
    ReadSubscriptionStatus,
    RetainBillingIdentity,
    SqlAlchemyAccessUsageStore,
    StartTrial,
    TrialActivationStatus,
)
from pin.contexts.billing.application.contracts import ReserveUsage
from pin.contexts.billing.domain.access import Operation
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessExpiryNoticeRecord,
    AccessPeriodRecord,
    RetainedBillingIdentityRecord,
)
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue19_lifecycle_"
DATABASE_USER = "pin_test"
DATABASE_PORT = 51017
DATABASE_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


def _runtime_database_url() -> str:
    value = os.getenv("PIN_TEST_ISSUE19_LIFECYCLE_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_ISSUE19_LIFECYCLE_DATABASE_URL to the owned runtime database")
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
    if not name.startswith(DATABASE_PREFIX) or name == "pin_model_adapter_20261013":
        pytest.fail("Issue19 runtime proof requires its own pin_issue19_lifecycle_ database")
    if user != DATABASE_USER or port != DATABASE_PORT or version != "18.6":
        pytest.fail("Issue19 runtime PostgreSQL does not match the assigned user, port and version")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATABASE_DIRECTORY.casefold():
        pytest.fail("Issue19 runtime PostgreSQL is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def lifecycle_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _runtime_database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    # This fixture never drops/resets its assigned database or its rows.
    from pin.persistence import run_local_async

    run_local_async(engine.dispose)


class _NoQuietHours:
    async def quiet_until(self, _owner_id, _at):
        return None


def test_returning_owner_restores_same_paid_period_and_remaining_quota(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 710_000_000 + (owner_id.int % 100_000_000)
    now = datetime.now(UTC).replace(microsecond=0)

    async def scenario() -> None:
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            trial = await store.activate_trial(StartTrial(owner_id, telegram_user_id, now))
            assert trial.status is TrialActivationStatus.ACTIVATED
            paid_start = now + timedelta(minutes=1)
            paid_end = paid_start + timedelta(days=30)
            assert await store.activate_paid_period(
                ActivatePaidPeriod(owner_id, f"return-{owner_id}", paid_start, paid_end)
            )
            held = await store.reserve_usage(
                ReserveUsage(
                    owner_id,
                    f"held-{owner_id}",
                    Operation.AI_TURN,
                    1,
                    0,
                    paid_start,
                    paid_start + timedelta(minutes=5),
                )
            )
            assert held.reservation is not None
            assert held.reservation.period_id == f"return-{owner_id}"
            await store.settle_usage(
                owner_id=owner_id,
                request_id=f"held-{owner_id}",
                ai_turns=1,
                voice_seconds=0,
                at=paid_start + timedelta(seconds=30),
            )
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None
            account.deleted_at = paid_start + timedelta(minutes=2)
        async with lifecycle_sessions() as session, session.begin():
            restored = await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, telegram_user_id, paid_start + timedelta(minutes=3))
            )
            assert restored.status is TrialActivationStatus.ALREADY_ACTIVE
            assert restored.period_id == f"return-{owner_id}"
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            status = await store.read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_start + timedelta(minutes=4))
            )
            assert status.active
            assert status.period_id == f"return-{owner_id}"
            assert status.ai_turns_remaining == 999
            assert status.voice_seconds_remaining == 3600
            count = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(AccessPeriodRecord.owner_id == owner_id)
            )
            assert count == 2
            replay = await store.activate_trial(
                StartTrial(owner_id, telegram_user_id, paid_start + timedelta(minutes=5))
            )
            assert replay.status is TrialActivationStatus.ALREADY_ACTIVE
            again = await store.read_subscription_status(
                ReadSubscriptionStatus(owner_id, paid_start + timedelta(minutes=6))
            )
            assert again.ai_turns_remaining == 999

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_paid_only_return_resolves_canonical_owner_without_resetting_quota(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    telegram_user_id = 720_000_000 + (owner_id.int % 100_000_000)
    now = datetime.now(UTC).replace(microsecond=0)
    period_id = f"return-paid-only-{owner_id}"

    async def scenario() -> None:
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert await store.activate_paid_period(
                ActivatePaidPeriod(
                    owner_id, period_id, now - timedelta(days=10), now + timedelta(days=20)
                )
            )
            held = await store.reserve_usage(
                ReserveUsage(
                    owner_id,
                    f"return-paid-only-held-{owner_id}",
                    Operation.AI_TURN,
                    1,
                    0,
                    now,
                    now + timedelta(minutes=5),
                )
            )
            assert held.reservation is not None
            assert held.reservation.period_id == period_id
            assert await store.retain_owner_for_return(
                RetainBillingIdentity(owner_id, telegram_user_id, now)
            )
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None
            account.deleted_at = now + timedelta(minutes=1)

        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert await store.resolve_returning_owner(telegram_user_id) == owner_id
            retained = await session.get(RetainedBillingIdentityRecord, owner_id)
            assert retained is not None
            assert retained.telegram_user_id == telegram_user_id
            assert (
                await store.activate_trial(
                    StartTrial(owner_id, telegram_user_id, now + timedelta(minutes=2))
                )
            ).status is TrialActivationStatus.ALREADY_ACTIVE

        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            status = await store.read_subscription_status(
                ReadSubscriptionStatus(owner_id, now + timedelta(minutes=3))
            )
            assert status.active
            assert status.period_id == period_id
            assert status.ai_turns_remaining == 999
            count = await session.scalar(
                select(func.count())
                .select_from(AccessPeriodRecord)
                .where(AccessPeriodRecord.owner_id == owner_id)
            )
            assert count == 1

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_deleted_telegram_start_restores_retained_paid_owner(lifecycle_sessions):
    from sqlalchemy import delete

    from pin.adapters.telegram.ingress import (
        PostgresTelegramOwnerResolver,
        TelegramOwnerRecord,
        persist_update,
    )
    from pin.contexts.billing.infrastructure.models import TrialActivationRecord
    from pin.contexts.planning.infrastructure.models import PlanItemRecord

    owner_id = uuid4()
    telegram_user_id = 730_000_000 + (owner_id.int % 100_000_000)
    bot_id = 7_300_000_000 + (owner_id.int % 10_000_000)
    now = datetime.now(UTC).replace(microsecond=0)
    period_id = f"ingress-return-{owner_id}"

    async def scenario():
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert (
                await store.activate_trial(
                    StartTrial(owner_id, telegram_user_id, now - timedelta(days=60))
                )
            ).status is TrialActivationStatus.ACTIVATED
            assert await store.activate_paid_period(
                ActivatePaidPeriod(
                    owner_id,
                    period_id,
                    now - timedelta(days=10),
                    now + timedelta(days=20),
                )
            )
            reservation = await store.reserve_usage(
                ReserveUsage(
                    owner_id,
                    f"{period_id}-used-turn",
                    Operation.AI_TURN,
                    1,
                    0,
                    now - timedelta(days=9),
                    now - timedelta(days=9) + timedelta(minutes=5),
                )
            )
            assert reservation.reservation is not None
            await store.settle_usage(
                owner_id=owner_id,
                request_id=f"{period_id}-used-turn",
                ai_turns=1,
                voice_seconds=0,
                at=now - timedelta(days=9) + timedelta(seconds=30),
            )
            assert await store.retain_owner_for_return(
                RetainBillingIdentity(owner_id, telegram_user_id, now - timedelta(days=1))
            )
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None
            account.deleted_at = now - timedelta(hours=1)
            await session.execute(
                delete(TelegramOwnerRecord).where(
                    TelegramOwnerRecord.bot_id == bot_id,
                    TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                )
            )

        payload = {
            "update_id": 7_300_000_000 + (owner_id.int % 10_000_000),
            "message": {
                "message_id": 1,
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Return"},
                "chat": {"id": telegram_user_id, "type": "private"},
                "text": "/start",
            },
        }
        async with lifecycle_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload=payload,
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted
            mapping = await session.get(TelegramOwnerRecord, (bot_id, telegram_user_id))
            assert mapping is not None and mapping.owner_id == owner_id
            restored = await session.get(AccessAccountRecord, owner_id)
            assert restored is not None and restored.deleted_at is None
            status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                ReadSubscriptionStatus(owner_id, now)
            )
            assert status.active and status.period_id == period_id
            assert status.ai_turns_remaining == 999
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TrialActivationRecord)
                    .where(TrialActivationRecord.owner_id == owner_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PlanItemRecord)
                    .where(PlanItemRecord.owner_id == owner_id)
                )
                == 0
            )

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_subscription_command_is_deterministic_and_uses_current_paid_status(lifecycle_sessions):
    from pin.adapters.telegram.application_worker import (
        claim_application_job,
        process_application_job,
    )
    from pin.adapters.telegram.ingress import (
        PostgresTelegramOwnerResolver,
        TelegramOwnerRecord,
        persist_update,
    )
    from pin.contexts.identity.infrastructure.models import PreferencesRecord
    from pin.platform.contracts import EventEnvelope
    from pin.platform.models import Job

    class NoModelCalls:
        async def interpret(self, *_args, **_kwargs):
            raise AssertionError("/subscription must not invoke a model")

    owner_id = uuid4()
    telegram_user_id = 740_000_000 + (owner_id.int % 100_000_000)
    bot_id = 7_400_000_000 + (owner_id.int % 10_000_000)
    now = datetime.now(UTC).replace(microsecond=0)
    period_id = f"subscription-{owner_id}"

    async def scenario():
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert await store.activate_paid_period(
                ActivatePaidPeriod(
                    owner_id,
                    period_id,
                    now - timedelta(days=10),
                    now + timedelta(days=20),
                )
            )
            reservation = await store.reserve_usage(
                ReserveUsage(
                    owner_id,
                    f"{period_id}-used-turn",
                    Operation.AI_TURN,
                    1,
                    0,
                    now - timedelta(days=9),
                    now - timedelta(days=9) + timedelta(minutes=5),
                )
            )
            assert reservation.reservation is not None
            await store.settle_usage(
                owner_id=owner_id,
                request_id=f"{period_id}-used-turn",
                ai_turns=1,
                voice_seconds=0,
                at=now - timedelta(days=9) + timedelta(seconds=30),
            )
            session.add(
                PreferencesRecord(
                    owner_id=owner_id,
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=1,
                    reminder_elapsed_hours=1,
                    quiet_start=datetime.strptime("22:00", "%H:%M").time(),
                    quiet_end=datetime.strptime("08:00", "%H:%M").time(),
                    review_time=datetime.strptime("09:00", "%H:%M").time(),
                    version=1,
                )
            )
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    owner_id=owner_id,
                )
            )

        payload = {
            "update_id": 7_400_000_000 + (owner_id.int % 10_000_000),
            "message": {
                "message_id": 1,
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Member"},
                "chat": {"id": telegram_user_id, "type": "private"},
                "text": "/subscription",
            },
        }
        async with lifecycle_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload=payload,
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted

        async with lifecycle_sessions() as session, session.begin():
            update_job = await session.scalar(
                select(Job).where(
                    Job.deduplication_key
                    == f"telegram:{bot_id}:{payload['update_id']}"
                )
            )
            assert update_job is not None
            update_job.priority = 100

        claim = await claim_application_job(
            lifecycle_sessions, worker_id=f"subscription-{owner_id}"
        )
        assert claim is not None
        await process_application_job(
            lifecycle_sessions, claim, now=now, assistant=NoModelCalls()
        )
        async with lifecycle_sessions() as session:
            reply = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.causation_id == claim.job_id,
                )
            )
            assert reply is not None
            envelope = EventEnvelope.from_dict(reply.payload)
            text = envelope.payload.get("plain_text")
            assert isinstance(text, str)
            assert "Subscription is active" in text
            assert "999" in text and "60 min 0 sec" in text

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_expiry_warnings_are_idempotent_truthful_and_quiet_aware(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    period_id = f"notice-{owner_id}"
    ends_at = now + timedelta(hours=24)

    async def scenario() -> None:
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert await store.activate_paid_period(
                ActivatePaidPeriod(owner_id, period_id, now - timedelta(days=29), ends_at)
            )
            assert await store.enqueue_due_access_notices(at=now) == 1
            assert await store.enqueue_due_access_notices(at=now) == 0
        async with lifecycle_sessions() as session, session.begin():
            rows = list(
                (
                    await session.scalars(
                        select(AccessExpiryNoticeRecord).where(
                            AccessExpiryNoticeRecord.owner_id == owner_id
                        )
                    )
                ).all()
            )
            assert len(rows) == 1
            notice = rows[0]
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "billing.access_notice.due",
                    OutboxEvent.owner_id == owner_id,
                )
            )
            assert event is not None
            assert event.payload["payload"]["notice_id"] == str(notice.id)
            early = await SqlAlchemyAccessUsageStore(session).authorize_access_notice(
                CheckAccessNotice(owner_id, notice.id, period_id, now),
                quiet_policy=_NoQuietHours(),
            )
            assert early.kind is AccessNoticeKind.EXPIRY_24H
            assert early.status is AccessNoticeStatus.SEND
            assert early.remaining_seconds == 24 * 60 * 60

        late = ends_at - timedelta(minutes=30)
        async with lifecycle_sessions() as session, session.begin():
            store = SqlAlchemyAccessUsageStore(session)
            assert await store.enqueue_due_access_notices(at=late) == 1
        async with lifecycle_sessions() as session, session.begin():
            notices = list(
                (
                    await session.scalars(
                        select(AccessExpiryNoticeRecord)
                        .where(AccessExpiryNoticeRecord.owner_id == owner_id)
                        .order_by(AccessExpiryNoticeRecord.kind)
                    )
                ).all()
            )
            assert len(notices) == 2
            by_kind = {AccessNoticeKind(item.kind): item for item in notices}
            store = SqlAlchemyAccessUsageStore(session)
            stale = await store.authorize_access_notice(
                CheckAccessNotice(
                    owner_id, by_kind[AccessNoticeKind.EXPIRY_24H].id, period_id, late
                ),
                quiet_policy=_NoQuietHours(),
            )
            current = await store.authorize_access_notice(
                CheckAccessNotice(
                    owner_id, by_kind[AccessNoticeKind.EXPIRY_1H].id, period_id, late
                ),
                quiet_policy=_NoQuietHours(),
            )
            assert stale.status is AccessNoticeStatus.SKIP
            assert current.status is AccessNoticeStatus.SEND
            assert current.remaining_seconds == 30 * 60

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_obsolete_delivery_outcome_cannot_restart_resumed_cycle(lifecycle_sessions):
    from pin.contexts.accountability.infrastructure.models import (
        AccountabilityIntentRecord,
        AccountabilityOutcomeReceiptRecord,
        CheckInCycleRecord,
    )
    from pin.contexts.accountability.infrastructure.scheduler import (
        apply_delivery_outcome,
        record_delivery_attempt_result,
    )
    from pin.persistence import run_local_async

    async def scenario():
        owner, cycle_id, intent_id, old_event, current_event = (uuid4() for _ in range(5))
        at = datetime.now(UTC)
        async with lifecycle_sessions() as session, session.begin():
            session.add(
                CheckInCycleRecord(
                    id=cycle_id,
                    owner_id=owner,
                    occurrence_id=uuid4(),
                    occurrence_version=1,
                    kind="appointment",
                    title="Resumed",
                    expected_end_at=at,
                    state="open",
                )
            )
            session.add(
                AccountabilityIntentRecord(
                    id=intent_id,
                    owner_id=owner,
                    cycle_id=cycle_id,
                    slot="initial",
                    state="scheduled",
                    due_at=at,
                    dispatch_version=2,
                )
            )
        async with lifecycle_sessions() as session, session.begin():
            assert await apply_delivery_outcome(
                session,
                event_id=old_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="confirmed",
                occurred_at=at,
                attempt_number=1,
                expected_dispatch_version=1,
            )
        async with lifecycle_sessions() as session, session.begin():
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            await record_delivery_attempt_result(
                session,
                intent_id=intent_id,
                owner_id=owner,
                outcome="unknown",
                retrying=False,
                failure_code="stale_worker",
                expected_dispatch_version=1,
            )
            await session.refresh(intent)
            assert intent.state == "scheduled" and intent.failure_code is None
            assert cycle.initial_confirmed_at is None and cycle.nudge_due_at is None
            assert intent.state == "scheduled" and intent.last_outcome_attempt == 0
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AccountabilityOutcomeReceiptRecord)
                    .where(AccountabilityOutcomeReceiptRecord.event_id == old_event)
                )
                == 1
            )
            assert not await apply_delivery_outcome(
                session,
                event_id=old_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="confirmed",
                occurred_at=at,
                attempt_number=1,
                expected_dispatch_version=1,
            )
            assert await apply_delivery_outcome(
                session,
                event_id=current_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="confirmed",
                occurred_at=at,
                attempt_number=2,
                expected_dispatch_version=2,
            )
        async with lifecycle_sessions() as session:
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            assert cycle.initial_confirmed_at == at
            assert cycle.nudge_due_at == at + timedelta(hours=1)

    run_local_async(scenario)


def test_notice_composition_receipts_replay_and_expired_preparation(lifecycle_sessions):
    from pin.adapters.telegram.access_notices import prepare_access_notice, schedule_access_notices
    from pin.contexts.notifications.application.delivery import TelegramReplySend
    from pin.persistence import run_local_async
    from pin.platform.contracts import EventEnvelope

    class Destination:
        async def resolve_destination(self, session, owner_id):
            return 12345

    async def scenario():
        owner = uuid4()
        at = datetime.now(UTC)
        end = at + timedelta(hours=24)
        async with lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(owner, f"composition-{owner}", end - timedelta(days=30), end)
            )
        async with lifecycle_sessions() as session, session.begin():
            await schedule_access_notices(session, Destination(), at=at)
        async with lifecycle_sessions() as session, session.begin():
            await schedule_access_notices(session, Destination(), at=at)
            replies = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.owner_id == owner,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                ).all()
            )
            assert len(replies) == 1
            envelope = EventEnvelope.from_dict(replies[0].payload)
            assert envelope.payload["requires_preparation"] is True
            assert envelope.payload["chat_id"] == 12345
            result = await prepare_access_notice(
                session,
                envelope,
                TelegramReplySend(12345, "/subscription", "/subscription"),
                end + timedelta(seconds=1),
            )
            assert result.message is None and result.deferred_until is None
            assert result.reason == "access_notice_obsolete"

    run_local_async(scenario)


def test_activation_consumer_receipts_current_and_expired_periods(lifecycle_sessions):
    from pin.adapters.telegram.access_resume import consume_access_activations
    from pin.persistence import run_local_async
    from pin.platform.models import OutboxConsumption

    async def scenario():
        owner = uuid4()
        at = datetime.now(UTC)
        async with lifecycle_sessions() as session, session.begin():
            await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(owner, f"activation-{owner}", at, at + timedelta(days=30))
            )
        async with lifecycle_sessions() as session, session.begin():
            await consume_access_activations(session, at=at + timedelta(seconds=1))
        async with lifecycle_sessions() as session, session.begin():
            await consume_access_activations(session, at=at + timedelta(days=31))
            event_id = await session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.owner_id == owner,
                    OutboxEvent.event_type == "billing.access_period.activated",
                )
            )
            count = await session.scalar(
                select(func.count())
                .select_from(OutboxConsumption)
                .where(
                    OutboxConsumption.event_id == event_id,
                    OutboxConsumption.consumer_name == "telegram.access_resume",
                )
            )
            assert count == 1

    run_local_async(scenario)


def test_retryable_initial_survives_expiry_skip_then_resumes_once(lifecycle_sessions):
    from datetime import date

    from pin.contexts.accountability.application.contracts import CurrentOccurrence
    from pin.contexts.accountability.infrastructure.models import (
        AccountabilityIntentRecord,
        CheckInCycleRecord,
    )
    from pin.contexts.accountability.infrastructure.scheduler import (
        apply_delivery_outcome,
        mark_delivery_intent_skipped,
        record_delivery_attempt_result,
        resume_initials_after_access,
    )
    from pin.persistence import run_local_async

    async def scenario():
        owner = uuid4()
        at = datetime.now(UTC)
        occurrence_id, cycle_id, intent_id = uuid4(), uuid4(), uuid4()
        failed_event, resumed_event = uuid4(), uuid4()
        occurrence = CurrentOccurrence(
            owner,
            occurrence_id,
            1,
            "appointment",
            "open",
            "Returned appointment",
            "UTC",
            date.today(),
            at - timedelta(hours=3),
            at,
            None,
        )
        async with lifecycle_sessions() as session, session.begin():
            session.add(
                CheckInCycleRecord(
                    id=cycle_id,
                    owner_id=owner,
                    occurrence_id=occurrence_id,
                    occurrence_version=1,
                    kind="appointment",
                    title="Returned appointment",
                    expected_end_at=at,
                    state="open",
                )
            )
            session.add(
                AccountabilityIntentRecord(
                    id=intent_id,
                    owner_id=owner,
                    cycle_id=cycle_id,
                    slot="initial",
                    state="sending",
                    due_at=at - timedelta(minutes=30),
                    attempt_count=1,
                )
            )
        async with lifecycle_sessions() as session, session.begin():
            await record_delivery_attempt_result(
                session,
                intent_id=intent_id,
                owner_id=owner,
                outcome="failed",
                retrying=True,
                failure_code="telegram_temporary_failure",
                expected_dispatch_version=1,
            )
            assert await mark_delivery_intent_skipped(
                session,
                intent_id=intent_id,
                owner_id=owner,
                reason="access_expired",
                expected_dispatch_version=1,
            )
            assert await apply_delivery_outcome(
                session,
                event_id=failed_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="failed",
                occurred_at=at,
                attempt_number=1,
                retrying=True,
                expected_dispatch_version=1,
            )
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            assert intent is not None
            assert intent.state == "skipped" and intent.failure_code == "access_expired"
            assert intent.retryable_after_access
        async with lifecycle_sessions() as session, session.begin():
            resumed = await resume_initials_after_access(
                session,
                owner_id=owner,
                period_id=f"resumed-{owner}",
                current_occurrences=(occurrence,),
                now=at + timedelta(days=1),
            )
            assert resumed == 1
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            assert intent is not None
            assert intent.state == "scheduled"
            assert intent.dispatch_version == 2
            assert not intent.retryable_after_access
            assert intent.due_at == at + timedelta(days=1)
            assert await resume_initials_after_access(
                session,
                owner_id=owner,
                period_id=f"resumed-{owner}",
                current_occurrences=(occurrence,),
                now=at + timedelta(days=1),
            ) == 0
            assert await apply_delivery_outcome(
                session,
                event_id=resumed_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="confirmed",
                occurred_at=at + timedelta(days=1),
                attempt_number=2,
                expected_dispatch_version=2,
            )
            assert not await apply_delivery_outcome(
                session,
                event_id=resumed_event,
                owner_id=owner,
                intent_id=intent_id,
                outcome="confirmed",
                occurred_at=at + timedelta(days=1),
                attempt_number=2,
                expected_dispatch_version=2,
            )
        async with lifecycle_sessions() as session:
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            assert cycle is not None
            assert cycle.initial_confirmed_at == at + timedelta(days=1)
            assert cycle.nudge_due_at == at + timedelta(days=1, hours=1)
            assert not cycle.nudge_slot_used

    run_local_async(scenario)


def test_terminal_and_planning_closure_states_clear_retry_marker(lifecycle_sessions):
    from datetime import date

    from pin.contexts.accountability.api import CurrentOccurrence, apply_delivery_outcome
    from pin.contexts.accountability.infrastructure.models import (
        AccountabilityIntentRecord,
        CheckInCycleRecord,
    )
    from pin.contexts.accountability.infrastructure.scheduler import (
        record_delivery_attempt_result,
        synchronize_occurrence,
    )
    from pin.persistence import run_local_async

    async def scenario():
        at = datetime.now(UTC)
        outcomes = ("confirmed", "unknown", "blocked", "failed")
        records = []
        async with lifecycle_sessions() as session, session.begin():
            for outcome in outcomes:
                owner, occurrence_id, cycle_id, intent_id = (uuid4() for _ in range(4))
                cycle = CheckInCycleRecord(
                    id=cycle_id,
                    owner_id=owner,
                    occurrence_id=occurrence_id,
                    occurrence_version=1,
                    kind="appointment",
                    title="Terminal state guard",
                    expected_end_at=at,
                    state="open",
                )
                intent = AccountabilityIntentRecord(
                    id=intent_id,
                    owner_id=owner,
                    cycle_id=cycle_id,
                    slot="initial",
                    state="skipped",
                    due_at=at,
                    dispatch_version=1,
                    retryable_after_access=True,
                    failure_code="access_expired",
                )
                session.add_all([cycle, intent])
                records.append((owner, cycle_id, intent_id, outcome))
            closed_owner, closed_occurrence, closed_cycle, closed_intent = (
                uuid4() for _ in range(4)
            )
            session.add_all(
                [
                    CheckInCycleRecord(
                        id=closed_cycle,
                        owner_id=closed_owner,
                        occurrence_id=closed_occurrence,
                        occurrence_version=1,
                        kind="appointment",
                        title="Closed occurrence guard",
                        expected_end_at=at,
                        state="open",
                    ),
                    AccountabilityIntentRecord(
                        id=closed_intent,
                        owner_id=closed_owner,
                        cycle_id=closed_cycle,
                        slot="initial",
                        state="skipped",
                        due_at=at,
                        dispatch_version=1,
                        retryable_after_access=True,
                        failure_code="access_expired",
                    ),
                ]
            )

        async with lifecycle_sessions() as session, session.begin():
            for owner, _cycle_id, intent_id, outcome in records:
                await record_delivery_attempt_result(
                    session,
                    intent_id=intent_id,
                    owner_id=owner,
                    outcome=outcome,
                    retrying=False,
                    failure_code=f"terminal_{outcome}",
                    expected_dispatch_version=1,
                )
                intent = await session.get(AccountabilityIntentRecord, intent_id)
                assert intent is not None and not intent.retryable_after_access
                assert await apply_delivery_outcome(
                    session,
                    event_id=uuid4(),
                    owner_id=owner,
                    intent_id=intent_id,
                    outcome=outcome,
                    occurred_at=at,
                    attempt_number=1,
                    retrying=False,
                    expected_dispatch_version=1,
                )
                await session.refresh(intent)
                assert not intent.retryable_after_access

            completed = CurrentOccurrence(
                closed_owner,
                closed_occurrence,
                1,
                "appointment",
                "completed",
                "Closed occurrence guard",
                "UTC",
                date.today(),
                at - timedelta(hours=2),
                at,
                None,
            )
            await synchronize_occurrence(session, completed, now=at)
            closed_intent_row = await session.get(AccountabilityIntentRecord, closed_intent)
            closed_cycle_row = await session.get(CheckInCycleRecord, closed_cycle)
            assert closed_intent_row is not None and not closed_intent_row.retryable_after_access
            assert closed_cycle_row is not None and closed_cycle_row.state == "completed"

    run_local_async(scenario)


def test_access_activation_enqueues_one_summary_job_per_period(lifecycle_sessions):
    from pin.adapters.telegram.access_resume import consume_access_activations
    from pin.persistence import run_local_async
    from pin.platform.models import Job

    async def scenario():
        owner = uuid4()
        at = datetime.now(UTC)
        period_id = f"summary-{owner}"
        async with lifecycle_sessions() as session, session.begin():
            await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(owner, period_id, at, at + timedelta(days=30))
            )
        async with lifecycle_sessions() as session, session.begin():
            assert await consume_access_activations(session, at=at + timedelta(seconds=1)) == 1
        async with lifecycle_sessions() as session, session.begin():
            assert await consume_access_activations(session, at=at + timedelta(seconds=2)) == 0
            summaries = list(
                (
                    await session.scalars(
                        select(Job).where(
                            Job.owner_id == owner,
                            Job.kind == "notifications.deliver_recovery_summary",
                        )
                    )
                ).all()
            )
            assert len(summaries) == 1
            assert summaries[0].payload == {
                "owner_id": str(owner),
                "episode_key": period_id,
            }
            assert summaries[0].deduplication_key == f"recovery-summary:{owner}:{period_id}"

    run_local_async(scenario)


def test_external_failure_expires_and_resumes_through_durable_events(lifecycle_sessions):
    from datetime import time

    from pin.adapters.telegram.access_resume import consume_access_activations
    from pin.contexts.accountability.api import (
        CurrentOccurrence,
        ReviewProfile,
        consume_delivery_outcomes,
        schedule_due_accountability,
    )
    from pin.contexts.accountability.infrastructure.models import (
        AccountabilityIntentRecord,
        CheckInCycleRecord,
    )
    from pin.contexts.notifications.api import (
        ACCOUNTABILITY_DELIVERY_JOB_KIND,
        KnownDeliveryFailure,
        TelegramReplySend,
        deliver_accountability_job,
    )
    from pin.contexts.notifications.infrastructure.models import (
        AccountabilityDeliveryAttemptRecord,
    )
    from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
    from pin.platform.jobs import JobLane, claim_job, enqueue_job
    from pin.platform.models import Job, OutboxConsumption

    class CurrentReads:
        def __init__(self, current):
            self.current = current

        async def get_current_occurrence(self, owner, occurrence, *, for_update=False):
            if owner == self.current.owner_id and occurrence == self.current.occurrence_id:
                return self.current
            return None

    class NoUntimed:
        async def list_today_open_untimed(
            self, _owner, *, profile_zone, at, for_update=False
        ):
            return ()

    class FailingTransport:
        calls = 0

        async def send_reply(self, _message):
            self.calls += 1
            raise KnownDeliveryFailure(
                "temporary_provider_rejection", retry_after=timedelta(seconds=30)
            )

    class SuccessfulTransport:
        calls = 0

        async def send_reply(self, _message):
            self.calls += 1
            return "native-test-message"

    owner_id = uuid4()
    occurrence_id, plan_id, cycle_id, intent_id = (uuid4() for _ in range(4))
    destination_id = 750_000_000 + owner_id.int % 100_000_000
    base_now = datetime.now(UTC).replace(microsecond=0)
    ends_at = base_now - timedelta(hours=1)
    expiry_at = base_now + timedelta(minutes=2)
    old_period = f"failure-period-{owner_id}"
    new_period = f"renewed-period-{owner_id}"
    local_start = base_now - timedelta(hours=2)
    local_end = ends_at
    profile = ReviewProfile(
        owner_id=owner_id,
        home_zone="UTC",
        language="en",
        review_time=time(9, 0),
        quiet_start=time(23, 59),
        quiet_end=time(0, 1),
        destination_id=destination_id,
    )
    current = CurrentOccurrence(
        owner_id,
        occurrence_id,
        1,
        "appointment",
        "open",
        "Persisted recovered appointment",
        "UTC",
        local_start.date(),
        local_start,
        ends_at,
        None,
    )
    current_reads = CurrentReads(current)
    today_untimed = NoUntimed()
    failing_transport = FailingTransport()
    successful_transport = SuccessfulTransport()

    async def read_profile(_session, requested_owner):
        return profile if requested_owner == owner_id else None

    async def render(_session, send, _at):
        return TelegramReplySend(send.destination_id, send.plain_text, send.plain_text)

    async def add_delivery_job(dispatch_version, available_at):
        async with lifecycle_sessions() as session, session.begin():
            job = await enqueue_job(
                session,
                lane=JobLane.DELIVERY,
                kind=ACCOUNTABILITY_DELIVERY_JOB_KIND,
                payload={"intent_id": str(intent_id), "dispatch_version": dispatch_version},
                deduplication_key=f"issue19-accountability:{intent_id}:{dispatch_version}",
                owner_id=owner_id,
                priority=100,
            )
            job.available_at = available_at
            return job.id

    async def claim_specific_delivery_job(worker_id):
        async with lifecycle_sessions() as session, session.begin():
            return await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id=worker_id,
                lease_for=timedelta(seconds=45),
            )

    async def scenario():
        async with lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(
                    owner_id, old_period, expiry_at - timedelta(days=30), expiry_at
                )
            )
            session.add(
                PlanItemRecord(
                    id=plan_id,
                    owner_id=owner_id,
                    kind="appointment",
                    title="Persisted recovered appointment",
                    version=1,
                )
            )
            await session.flush()
            session.add_all(
                [
                    OccurrenceRecord(
                        id=occurrence_id,
                        owner_id=owner_id,
                        plan_item_id=plan_id,
                        kind="appointment",
                        event_zone="UTC",
                        local_date=local_start.date(),
                        local_time=local_start.time().replace(tzinfo=None),
                        fold=None,
                        end_local_date=local_end.date(),
                        end_local_time=local_end.time().replace(tzinfo=None),
                        end_fold=None,
                        starts_at=local_start,
                        ends_at=ends_at,
                        deadline_at=None,
                        duration_seconds=3600,
                        state="open",
                        version=1,
                        reminder_preferences_version=1,
                    ),
                    CheckInCycleRecord(
                        id=cycle_id,
                        owner_id=owner_id,
                        occurrence_id=occurrence_id,
                        occurrence_version=1,
                        kind="appointment",
                        title="Persisted recovered appointment",
                        expected_end_at=ends_at,
                        state="open",
                    ),
                    AccountabilityIntentRecord(
                        id=intent_id,
                        owner_id=owner_id,
                        cycle_id=cycle_id,
                        slot="initial",
                        state="scheduled",
                        due_at=base_now - timedelta(minutes=1),
                        dispatch_version=1,
                    ),
                ]
            )

        await add_delivery_job(1, base_now - timedelta(seconds=1))
        first_claim = await claim_specific_delivery_job(f"issue19-first-{owner_id}")
        assert first_claim is not None and first_claim.payload["dispatch_version"] == 1
        first_outcome = await deliver_accountability_job(
            lifecycle_sessions,
            first_claim,
            failing_transport,
            profile_reader=read_profile,
            current_occurrences_factory=lambda _session: current_reads,
            today_untimed_factory=lambda _session: today_untimed,
            render=render,
            now=lambda: base_now,
        )
        assert first_outcome.value == "failed" and failing_transport.calls == 1
        async with lifecycle_sessions() as session:
            failed_attempt = await session.scalar(
                select(AccountabilityDeliveryAttemptRecord).where(
                    AccountabilityDeliveryAttemptRecord.intent_id == intent_id,
                    AccountabilityDeliveryAttemptRecord.dispatch_version == 1,
                )
            )
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            assert failed_attempt is not None and failed_attempt.outcome == "failed"
            assert intent is not None and intent.retryable_after_access

        # Make the durable retry available after its actual access period expires.
        async with lifecycle_sessions() as session, session.begin():
            retry_job = await session.scalar(
                select(Job).where(
                    Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                    Job.deduplication_key == f"issue19-accountability:{intent_id}:1",
                )
            )
            assert retry_job is not None
            retry_job.available_at = datetime.now(UTC) - timedelta(seconds=1)
        second_claim = await claim_specific_delivery_job(f"issue19-expired-{owner_id}")
        assert second_claim is not None
        expired_outcome = await deliver_accountability_job(
            lifecycle_sessions,
            second_claim,
            failing_transport,
            profile_reader=read_profile,
            current_occurrences_factory=lambda _session: current_reads,
            today_untimed_factory=lambda _session: today_untimed,
            render=render,
            now=lambda: expiry_at + timedelta(seconds=1),
        )
        assert expired_outcome.value == "skipped" and failing_transport.calls == 1

        # The FAILED event is consumed only after expiry skipped the intent.
        async with lifecycle_sessions() as session, session.begin():
            assert (
                await consume_delivery_outcomes(
                    session, at=expiry_at + timedelta(seconds=2)
                )
                == 1
            )
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            assert intent is not None
            assert intent.state == "skipped" and intent.failure_code == "access_expired"
            assert intent.retryable_after_access

        renewed_at = expiry_at + timedelta(minutes=5)
        async with lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(
                    owner_id,
                    new_period,
                    renewed_at,
                    renewed_at + timedelta(days=30),
                )
            )
        async with lifecycle_sessions() as session, session.begin():
            assert await consume_access_activations(session, at=renewed_at) >= 2
            activation_event_ids = select(OutboxEvent.id).where(
                OutboxEvent.owner_id == owner_id,
                OutboxEvent.event_type == "billing.access_period.activated",
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxConsumption)
                    .where(
                        OutboxConsumption.consumer_name == "telegram.access_resume",
                        OutboxConsumption.event_id.in_(activation_event_ids),
                    )
                )
                == 2
            )
            intent = await session.get(AccountabilityIntentRecord, intent_id)
            assert intent is not None
            assert intent.state == "scheduled" and intent.dispatch_version == 2
            assert not intent.retryable_after_access

        async with lifecycle_sessions() as session, session.begin():
            resumed_job = await session.scalar(
                select(Job).where(
                    Job.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND,
                    Job.deduplication_key == f"accountability:{intent_id}:2",
                )
            )
            assert resumed_job is not None
            resumed_job.priority = 100
            resumed_job.available_at = datetime.now(UTC) - timedelta(seconds=1)
        resumed_claim = await claim_specific_delivery_job(f"issue19-resumed-{owner_id}")
        assert resumed_claim is not None and resumed_claim.payload["dispatch_version"] == 2
        confirmed_at = renewed_at + timedelta(seconds=1)
        resumed_outcome = await deliver_accountability_job(
            lifecycle_sessions,
            resumed_claim,
            successful_transport,
            profile_reader=read_profile,
            current_occurrences_factory=lambda _session: current_reads,
            today_untimed_factory=lambda _session: today_untimed,
            render=render,
            now=lambda: confirmed_at,
        )
        assert resumed_outcome.value == "confirmed" and successful_transport.calls == 1
        async with lifecycle_sessions() as session, session.begin():
            assert await consume_delivery_outcomes(session, at=confirmed_at) == 1
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            assert cycle is not None and cycle.initial_confirmed_at == confirmed_at
            assert cycle.nudge_due_at == confirmed_at + timedelta(hours=1)
            assert not cycle.nudge_slot_used

        nudge_at = confirmed_at + timedelta(hours=1, seconds=1)
        async with lifecycle_sessions() as session, session.begin():
            assert await schedule_due_accountability(
                session,
                profiles=(profile,),
                today_untimed=today_untimed,
                now=nudge_at,
            ) == 1
        async with lifecycle_sessions() as session, session.begin():
            assert await schedule_due_accountability(
                session,
                profiles=(profile,),
                today_untimed=today_untimed,
                now=nudge_at,
            ) == 0
            cycle = await session.get(CheckInCycleRecord, cycle_id)
            assert cycle is not None and cycle.nudge_slot_used
            nudge = await session.scalar(
                select(AccountabilityIntentRecord).where(
                    AccountabilityIntentRecord.cycle_id == cycle_id,
                    AccountabilityIntentRecord.slot == "nudge",
                )
            )
            assert nudge is not None and nudge.state == "scheduled"
            assert nudge.due_at == cycle.nudge_due_at

    from pin.persistence import run_local_async

    run_local_async(scenario)


def test_paid_renewal_rearms_only_missed_reminders_for_upcoming_open_items(lifecycle_sessions):
    from pin.adapters.telegram.access_resume import consume_access_activations
    from pin.contexts.notifications.infrastructure.models import (
        DeliveryAttemptRecord,
        ReminderIntentRecord,
    )
    from pin.contexts.planning.infrastructure.models import (
        OccurrenceRecord,
        PlanItemRecord,
        ReminderRuleRecord,
    )
    from pin.platform.models import Job

    owner_id = uuid4()
    at = datetime.now(UTC).replace(microsecond=0)
    period_id = f"mixed-resume-{owner_id}"
    upcoming_plan_id, overdue_plan_id = uuid4(), uuid4()
    upcoming_occurrence_id, overdue_occurrence_id = uuid4(), uuid4()
    upcoming_intent_id, overdue_intent_id = uuid4(), uuid4()
    upcoming_attempt_id, overdue_attempt_id = uuid4(), uuid4()
    future_start = at + timedelta(days=2)
    future_end = future_start + timedelta(hours=1)
    overdue_start = at - timedelta(days=2)

    async def scenario():
        async with lifecycle_sessions() as session, session.begin():
            assert await SqlAlchemyAccessUsageStore(session).activate_paid_period(
                ActivatePaidPeriod(
                    owner_id,
                    period_id,
                    at - timedelta(days=29),
                    at + timedelta(days=1),
                )
            )
            session.add_all(
                [
                    PlanItemRecord(
                        id=upcoming_plan_id,
                        owner_id=owner_id,
                        kind="appointment",
                        title="Upcoming useful appointment",
                        version=1,
                    ),
                    PlanItemRecord(
                        id=overdue_plan_id,
                        owner_id=owner_id,
                        kind="task",
                        title="Unresolved overdue task",
                        version=1,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    OccurrenceRecord(
                        id=upcoming_occurrence_id,
                        owner_id=owner_id,
                        plan_item_id=upcoming_plan_id,
                        kind="appointment",
                        event_zone="UTC",
                        local_date=future_start.date(),
                        local_time=future_start.time().replace(tzinfo=None),
                        fold=None,
                        end_local_date=future_end.date(),
                        end_local_time=future_end.time().replace(tzinfo=None),
                        end_fold=None,
                        starts_at=future_start,
                        ends_at=future_end,
                        deadline_at=None,
                        duration_seconds=3600,
                        state="open",
                        version=1,
                        reminder_preferences_version=1,
                    ),
                    OccurrenceRecord(
                        id=overdue_occurrence_id,
                        owner_id=owner_id,
                        plan_item_id=overdue_plan_id,
                        kind="task",
                        event_zone="UTC",
                        local_date=overdue_start.date(),
                        local_time=overdue_start.time().replace(tzinfo=None),
                        fold=None,
                        end_local_date=None,
                        end_local_time=None,
                        end_fold=None,
                        starts_at=overdue_start,
                        ends_at=None,
                        deadline_at=None,
                        duration_seconds=None,
                        state="open",
                        version=1,
                        reminder_preferences_version=1,
                    ),
                ]
            )
            await session.flush()
            rules = [
                ReminderRuleRecord(
                    owner_id=owner_id,
                    occurrence_id=upcoming_occurrence_id,
                    offset_kind="elapsed_seconds",
                    offset_amount=3600,
                    source="explicit",
                    due_at=at - timedelta(hours=1),
                ),
                ReminderRuleRecord(
                    owner_id=owner_id,
                    occurrence_id=overdue_occurrence_id,
                    offset_kind="elapsed_seconds",
                    offset_amount=3600,
                    source="explicit",
                    due_at=at - timedelta(hours=2),
                ),
            ]
            session.add_all(rules)
            session.add_all(
                [
                    ReminderIntentRecord(
                        id=upcoming_intent_id,
                        owner_id=owner_id,
                        plan_item_id=upcoming_plan_id,
                        occurrence_id=upcoming_occurrence_id,
                        series_id=None,
                        plan_version=1,
                        occurrence_version=1,
                        preferences_version=1,
                        series_version=None,
                        offset_kind="elapsed_seconds",
                        offset_amount=3600,
                        source="explicit",
                        due_at=at - timedelta(hours=1),
                        not_before_at=None,
                        state="scheduled",
                        dispatch_version=1,
                        source_event_id=uuid4(),
                    ),
                    ReminderIntentRecord(
                        id=overdue_intent_id,
                        owner_id=owner_id,
                        plan_item_id=overdue_plan_id,
                        occurrence_id=overdue_occurrence_id,
                        series_id=None,
                        plan_version=1,
                        occurrence_version=1,
                        preferences_version=1,
                        series_version=None,
                        offset_kind="elapsed_seconds",
                        offset_amount=3600,
                        source="explicit",
                        due_at=at - timedelta(hours=2),
                        not_before_at=None,
                        state="scheduled",
                        dispatch_version=1,
                        source_event_id=uuid4(),
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    DeliveryAttemptRecord(
                        id=upcoming_attempt_id,
                        owner_id=owner_id,
                        intent_id=upcoming_intent_id,
                        reply_event_id=None,
                        recovery_episode_key=None,
                        target_id=76_000_001,
                        dispatch_version=1,
                        attempt_number=1,
                        check_in_cycle_id=None,
                        access_period_id="expired-period",
                        outcome="skipped",
                        failure_code="access_expired",
                        provider_message_id=None,
                        quiet_conflict=False,
                        started_at=at - timedelta(hours=1, minutes=1),
                        finished_at=at - timedelta(hours=1),
                    ),
                    DeliveryAttemptRecord(
                        id=overdue_attempt_id,
                        owner_id=owner_id,
                        intent_id=overdue_intent_id,
                        reply_event_id=None,
                        recovery_episode_key=None,
                        target_id=76_000_001,
                        dispatch_version=1,
                        attempt_number=1,
                        check_in_cycle_id=None,
                        access_period_id="expired-period",
                        outcome="skipped",
                        failure_code="access_expired",
                        provider_message_id=None,
                        quiet_conflict=False,
                        started_at=at - timedelta(hours=2, minutes=1),
                        finished_at=at - timedelta(hours=2),
                    ),
                ]
            )

        async with lifecycle_sessions() as session, session.begin():
            await consume_access_activations(session, at=at)
        async with lifecycle_sessions() as session, session.begin():
            await consume_access_activations(session, at=at + timedelta(seconds=1))
            upcoming = await session.get(ReminderIntentRecord, upcoming_intent_id)
            overdue = await session.get(ReminderIntentRecord, overdue_intent_id)
            assert upcoming is not None and upcoming.dispatch_version == 2
            assert overdue is not None and overdue.dispatch_version == 1
            summaries = list(
                (
                    await session.scalars(
                        select(Job).where(
                            Job.owner_id == owner_id,
                            Job.kind == "notifications.deliver_recovery_summary",
                        )
                    )
                ).all()
            )
            assert len(summaries) == 1
            assert summaries[0].payload == {
                "owner_id": str(owner_id),
                "episode_key": period_id,
            }

    from pin.persistence import run_local_async

    run_local_async(scenario)
