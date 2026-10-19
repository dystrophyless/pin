from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from processing_consent_fixtures import seed_accepted_processing_consent
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.adapters.telegram.application_worker import claim_application_job, process_application_job
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.application.contracts import CallStart, ReservationStatus
from pin.contexts.conversation.application.dto import ConversationContext, ModelFailure
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.domain.proposals import InputKind, SourceLanguage
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureConfirmationRecord,
    CaptureDraftRecord,
)
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.api import (
    PROCESSING_CONSENT_POLICY_VERSION,
    ProcessingAuthorization,
    SqlAlchemyIdentityStore,
    authorize_processing_consent,
    get_processing_consent,
    revoke_processing_consent,
)
from pin.contexts.identity.infrastructure.deletion import purge_owner
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.application.delivery import TelegramReplySend
from pin.contexts.notifications.infrastructure.delivery_worker import (
    DELIVERY_REPLY_JOB_KIND,
    deliver_reply_job,
    enqueue_due_reply_jobs,
)
from pin.contexts.notifications.infrastructure.models import DeliveryAttemptRecord
from pin.entrypoints.recovery import _invalidate_restored_consents
from pin.persistence.async_runner import run_local_async
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobLane, claim_job
from pin.platform.models import InboxUpdate, Job, OutboxEvent, RecoveryGate

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "PIN_TEST_CONSENT_DATABASE_URL"
DATABASE_PREFIX = "pin_consent_guard_"


def _database_url() -> str:
    value = os.getenv(DATABASE_ENV)
    if not value:
        pytest.skip(f"set {DATABASE_ENV} to the assigned disposable consent database")
    url = make_url(value)
    if (
        url.host not in {"127.0.0.1", "localhost"}
        or not url.database
        or not url.database.startswith(DATABASE_PREFIX)
    ):
        pytest.fail("consent PostgreSQL URL does not match the assigned disposable fixture")
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


@pytest.fixture(scope="module")
def consent_database_url() -> str:
    return _database_url()


def _factory(url: str, *, application_name: str) -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        url,
        connect_args={"application_name": application_name},
    )
    return engine, async_sessionmaker[AsyncSession](engine, expire_on_commit=False)


async def _seed_owner(
    sessions: async_sessionmaker[AsyncSession], *, accepted: bool
) -> tuple[int, int, UUID, ProcessingAuthorization | None]:
    bot_id = 7_100_000_000 + uuid4().int % 900_000_000
    telegram_user_id = 6_100_000_000 + uuid4().int % 900_000_000
    resolver = PostgresTelegramOwnerResolver()
    async with sessions() as session, session.begin():
        owner_id = await resolver.resolve(
            session, bot_id=bot_id, telegram_user_id=telegram_user_id
        )
        if not accepted:
            return bot_id, telegram_user_id, owner_id, None
        authorization = await seed_accepted_processing_consent(
            session,
            owner_id=owner_id,
            bot_id=bot_id,
            telegram_user_id=telegram_user_id,
        )
        return bot_id, telegram_user_id, owner_id, authorization


def _message(update_id: int, telegram_user_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": telegram_user_id, "type": "private"},
            "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Test"},
            "text": "Move my reminder to tomorrow",
        },
    }


def test_ingress_binds_only_the_current_accepted_grant(consent_database_url: str) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-ingress-{uuid4()}"
        )
        try:
            bot_id, actor, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            update_id = uuid4().int % 9_000_000_000_000_000
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=_message(update_id, actor),
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
            async with sessions() as session:
                inbox = await session.scalar(
                    select(InboxUpdate).where(
                        InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
                    )
                )
                job = await session.scalar(
                    select(Job).where(Job.deduplication_key == f"telegram:{bot_id}:{update_id}")
                )
                assert inbox is not None and job is not None
                assert inbox.owner_id == job.owner_id == owner_id
                assert (inbox.processing_grant_id, inbox.processing_policy_version) == (
                    authorization.grant_id,
                    authorization.policy_version,
                )
                assert (job.processing_grant_id, job.processing_policy_version) == (
                    authorization.grant_id,
                    authorization.policy_version,
                )

            bot_id, actor, owner_id, authorization = await _seed_owner(sessions, accepted=False)
            assert authorization is None
            update_id = uuid4().int % 9_000_000_000_000_000
            async with sessions() as session, session.begin():
                await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=_message(update_id, actor),
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
            async with sessions() as session:
                inbox = await session.scalar(
                    select(InboxUpdate).where(
                        InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
                    )
                )
                assert inbox is not None and inbox.owner_id == owner_id
                assert inbox.processing_grant_id is None
                assert inbox.processing_policy_version is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_grant_after_admission_does_not_reauthorize_legacy_work(consent_database_url: str) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-late-grant-{uuid4()}"
        )
        try:
            bot_id = 7_100_000_000 + uuid4().int % 900_000_000
            actor = 6_100_000_000 + uuid4().int % 900_000_000
            resolver = PostgresTelegramOwnerResolver()
            async with sessions() as session, session.begin():
                owner_id = await resolver.resolve(
                    session, bot_id=bot_id, telegram_user_id=actor
                )
            update_id = uuid4().int % 9_000_000_000_000_000
            async with sessions() as session, session.begin():
                await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=_message(update_id, actor),
                    owner_resolver=resolver,
                )
            async with sessions() as session, session.begin():
                later_grant = await seed_accepted_processing_consent(
                    session,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    telegram_user_id=actor,
                )
            async with sessions() as session:
                inbox = await session.scalar(
                    select(InboxUpdate).where(
                        InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
                    )
                )
                job = await session.scalar(
                    select(Job).where(Job.deduplication_key == f"telegram:{bot_id}:{update_id}")
                )
                assert inbox is not None and job is not None
                assert inbox.owner_id == job.owner_id == owner_id
                assert inbox.processing_grant_id is None
                assert inbox.processing_policy_version is None
                assert job.processing_grant_id is None
                assert job.processing_policy_version is None
                assert later_grant.grant_id != inbox.processing_grant_id
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_concurrent_first_mapping_retries_after_losing_transaction_rolls_back(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        from pin.adapters.telegram.ingress import persist_update_with_owner_retry

        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-owner-retry-{uuid4()}"
        )
        bot_id = 7_100_000_000 + uuid4().int % 900_000_000
        actor = 6_100_000_000 + uuid4().int % 900_000_000
        both_read_unmapped = asyncio.Event()
        initial_calls = 0
        base = PostgresTelegramOwnerResolver()

        class FirstMappingBarrier(PostgresTelegramOwnerResolver):
            async def resolve(
                self, session, *, bot_id: int, telegram_user_id: int, expected_owner_id=None
            ):
                nonlocal initial_calls
                if initial_calls < 2:
                    initial_calls += 1
                    if initial_calls == 2:
                        both_read_unmapped.set()
                    await both_read_unmapped.wait()
                return await base.resolve(
                    session,
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    expected_owner_id=expected_owner_id,
                )

        resolver = FirstMappingBarrier()
        try:
            first_id = uuid4().int % 9_000_000_000_000_000
            second_id = uuid4().int % 9_000_000_000_000_000
            await asyncio.gather(
                persist_update_with_owner_retry(
                    sessions,
                    bot_id=bot_id,
                    payload=_message(first_id, actor),
                    owner_resolver=resolver,
                ),
                persist_update_with_owner_retry(
                    sessions,
                    bot_id=bot_id,
                    payload=_message(second_id, actor),
                    owner_resolver=resolver,
                ),
            )
            async with sessions() as session:
                owners = list(
                    (
                        await session.scalars(
                            select(TelegramOwnerRecord.owner_id).where(
                                TelegramOwnerRecord.bot_id == bot_id,
                                TelegramOwnerRecord.telegram_user_id == actor,
                            )
                        )
                    ).all()
                )
                inbox_owners = list(
                    (
                        await session.scalars(
                            select(InboxUpdate.owner_id).where(
                                InboxUpdate.bot_id == bot_id,
                                InboxUpdate.update_id.in_([first_id, second_id]),
                            )
                        )
                    ).all()
                )
            assert len(owners) == 1
            assert len(inbox_owners) == 2 and set(inbox_owners) == set(owners)
        finally:
            await engine.dispose()

    run_local_async(scenario)


class _Allowance:
    def __init__(self) -> None:
        self.reserve_calls = 0

    async def reserve(self, **_: object) -> ReservationStatus:
        self.reserve_calls += 1
        return ReservationStatus.RESERVED

    async def settle(self, **_: object) -> None:
        return None

    async def refund(self, **_: object) -> None:
        return None


class _OneMalformedThenReply:
    def __init__(self, after_first: object | None = None) -> None:
        self.calls = 0
        self.after_first = after_first

    async def complete(self, _: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if self.after_first is not None:
            await self.after_first()
        return {
            "id": f"consent-provider-{uuid4()}",
            "model": ROUTE,
            "choices": [{"message": {"content": '{"kind":"bad"}'}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost": "0.000001",
            },
        }


def _context(owner_id: UUID, authorization: ProcessingAuthorization) -> ConversationContext:
    instant = datetime.now(UTC)
    return ConversationContext(
        owner_id=owner_id,
        processing_grant_id=authorization.grant_id,
        processing_policy_version=authorization.policy_version,
        request_id=f"consent-{uuid4()}",
        language=SourceLanguage.EN,
        input_kind=InputKind.TEXT,
        text="Move the reminder to tomorrow.",
        reference_date=date.fromisoformat(instant.date().isoformat()),
        reference_instant=instant,
        timezone="UTC",
    )


async def _withdraw(
    sessions: async_sessionmaker[AsyncSession], owner_id: UUID, *, expected_grant: UUID
) -> None:
    async with sessions() as session, session.begin():
        store = SqlAlchemyIdentityStore(session)
        current = await get_processing_consent(store, owner_id=owner_id)
        assert current.grant_id == expected_grant and current.policy_version is not None
        await revoke_processing_consent(
            store,
            owner_id=owner_id,
            expected_revision=current.revision,
            expected_grant_id=expected_grant,
            expected_policy_version=current.policy_version,
            revoked_at=datetime.now(UTC),
        )


def test_withdrawal_between_provider_calls_prevents_repair_without_erasing_receipt(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-repair-{uuid4()}"
        )
        try:
            _, _, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            recorder = SqlAlchemyProviderUsageRecorder(sessions)

            async def withdraw() -> None:
                await _withdraw(sessions, owner_id, expected_grant=authorization.grant_id)

            transport = _OneMalformedThenReply(after_first=withdraw)
            context = _context(owner_id, authorization)
            assistant = BoundedPlanningAssistant(
                transport=transport,
                usage=recorder,
                allowance=_Allowance(),
                now=lambda: datetime.now(UTC),
            )
            outcome = await assistant.interpret(context, at=datetime.now(UTC))
            assert isinstance(outcome, ModelFailure)
            assert transport.calls == 1
            async with sessions() as session:
                calls = list(
                    (
                        await session.scalars(
                            select(ProviderCallRecord).where(
                                ProviderCallRecord.owner_id == owner_id,
                                ProviderCallRecord.turn_id == context.request_id,
                            )
                        )
                    ).all()
                )
            assert len(calls) == 1
            assert calls[0].state == "reported"
            assert calls[0].cost_usd == Decimal("0.000001000000")
            assert calls[0].processing_policy_version == authorization.policy_version
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize("consent_state", ("revoked", "missing_row", "missing_owner"))
def test_revoked_or_missing_grant_denies_before_first_dispatch(
    consent_database_url: str, consent_state: str
) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-denied-{uuid4()}"
        )
        try:
            if consent_state == "revoked":
                _, _, owner_id, authorization = await _seed_owner(sessions, accepted=True)
                assert authorization is not None
                await _withdraw(sessions, owner_id, expected_grant=authorization.grant_id)
            elif consent_state == "missing_row":
                _, _, owner_id, absent_authorization = await _seed_owner(
                    sessions, accepted=False
                )
                assert absent_authorization is None
                authorization = ProcessingAuthorization(
                    owner_id, uuid4(), PROCESSING_CONSENT_POLICY_VERSION
                )
            else:
                owner_id = uuid4()
                authorization = ProcessingAuthorization(
                    owner_id, uuid4(), PROCESSING_CONSENT_POLICY_VERSION
                )

            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                # Keep Billing access available in every case so denial proves the
                # consent fence, rather than an unrelated missing-access result.
                session.add(AccessAccountRecord(owner_id=owner_id))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=f"consent-denial-{uuid4().hex}",
                        policy_kind="trial",
                        policy_version="test",
                        starts_at=now - timedelta(minutes=1),
                        ends_at=now + timedelta(days=2),
                        ai_turn_limit=5,
                        voice_second_limit=60,
                    )
                )

            transport = _OneMalformedThenReply()
            assistant = BoundedPlanningAssistant(
                transport=transport,
                usage=SqlAlchemyProviderUsageRecorder(sessions),
                allowance=SqlAlchemyTurnAllowance(sessions),
                now=lambda: datetime.now(UTC),
            )
            context = _context(owner_id, authorization)
            outcome = await assistant.interpret(context, at=datetime.now(UTC))
            assert isinstance(outcome, ModelFailure)
            assert transport.calls == 0
            async with sessions() as session:
                reservations = list(
                    (
                        await session.scalars(
                            select(UsageReservationRecord).where(
                                UsageReservationRecord.owner_id == owner_id,
                                UsageReservationRecord.request_id == context.request_id,
                            )
                        )
                    ).all()
                )
                calls = list(
                    (
                        await session.scalars(
                            select(ProviderCallRecord).where(
                                ProviderCallRecord.owner_id == owner_id,
                                ProviderCallRecord.turn_id == context.request_id,
                            )
                        )
                    ).all()
                )
            assert calls == []
            assert reservations == []
        finally:
            await engine.dispose()

    run_local_async(scenario)

async def _wait_for_blocker(
    engine: object, waiting_application: str, blocking_application: str
) -> None:
    async with engine.connect() as connection:
        async with asyncio.timeout(10):
            while True:
                blocked = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiting "
                        "WHERE waiting.application_name = :waiting_app "
                        "AND waiting.wait_event_type = 'Lock' AND EXISTS ( "
                        "SELECT 1 FROM unnest(pg_blocking_pids(waiting.pid)) AS blocked(pid) "
                        "JOIN pg_stat_activity AS blocker ON blocker.pid = blocked.pid "
                        "WHERE blocker.application_name = :blocking_app))"
                    ),
                    {
                        "waiting_app": waiting_application,
                        "blocking_app": blocking_application,
                    },
                )
                if blocked:
                    return
                await connection.rollback()
                await asyncio.sleep(0.01)


async def _wait_for_activity(engine: object, application_name: str, event: str) -> None:
    async with engine.connect() as connection:
        async with asyncio.timeout(10):
            while True:
                waiting = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE application_name = :name AND wait_event_type = 'Lock' "
                        "AND wait_event = :event)"
                    ),
                    {"name": application_name, "event": event},
                )
                if waiting:
                    return
                await connection.rollback()
                await asyncio.sleep(0.01)


async def _wait_for_activity_or_task_failure(
    engine: object,
    application_name: str,
    event: str,
    task: asyncio.Task,
) -> None:
    async with engine.connect() as connection:
        async with asyncio.timeout(10):
            while True:
                if task.done():
                    await task
                    pytest.fail("worker completed before reaching its database barrier")
                waiting = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE application_name = :name AND wait_event_type = 'Lock' "
                        "AND wait_event = :event)"
                    ),
                    {"name": application_name, "event": event},
                )
                if waiting:
                    return
                await connection.rollback()
                await asyncio.sleep(0.01)


def test_provider_marker_and_withdrawal_serialize_at_the_database_fence(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        suffix = uuid4().hex[:12]
        worker_name, revoke_name = f"consent-marker-{suffix}", f"consent-withdraw-{suffix}"
        engine, sessions = _factory(consent_database_url, application_name=worker_name)
        revoke_engine, revoke_sessions = _factory(
            consent_database_url, application_name=revoke_name
        )
        barrier_key = uuid4().int & ((1 << 62) - 1)
        holder = None
        start = None
        revoke = None
        function_name, trigger_name = f"consent_wait_{suffix}", f"consent_wait_{suffix}"
        function = f"conversation.{function_name}"
        try:
            _, _, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN IF NEW.owner_id = '" + str(owner_id) + "'::uuid THEN "
                        f"PERFORM pg_advisory_xact_lock({barrier_key}); "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger_name} BEFORE INSERT "
                        "ON conversation.provider_calls "
                        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
                    )
                )
            holder = await engine.connect()
            await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": barrier_key})
            call = CallStart(
                owner_id=owner_id,
                turn_id=f"barrier-{uuid4()}",
                call_id=uuid4(),
                call_number=1,
                model=ROUTE,
                created_at=datetime.now(UTC),
                processing_grant_id=authorization.grant_id,
                processing_policy_version=authorization.policy_version,
            )
            start = asyncio.create_task(SqlAlchemyProviderUsageRecorder(sessions).start_call(call))
            await _wait_for_activity_or_task_failure(
                engine, worker_name, "advisory", start
            )

            revoke = asyncio.create_task(
                _withdraw(revoke_sessions, owner_id, expected_grant=authorization.grant_id)
            )
            await _wait_for_blocker(revoke_engine, revoke_name, worker_name)
            assert not start.done() and not revoke.done()

            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key})
            await holder.commit()
            await start
            await revoke
            async with sessions() as session:
                current = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                row = await session.get(ProviderCallRecord, call.call_id)
            assert current.state == "revoked"
            assert row is not None and row.state == "started"
            assert row.processing_grant_id == authorization.grant_id
        finally:
            for task in (start, revoke):
                if task is not None and not task.done():
                    task.cancel()
            if start is not None or revoke is not None:
                await asyncio.gather(
                    *(task for task in (start, revoke) if task is not None),
                    return_exceptions=True,
                )
            if holder is not None:
                try:
                    await holder.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key}
                    )
                    await holder.commit()
                except Exception:
                    pass
                await holder.close()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"DROP TRIGGER IF EXISTS {trigger_name} "
                            "ON conversation.provider_calls"
                        )
                    )
                    await connection.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
            finally:
                await engine.dispose()
                await revoke_engine.dispose()

    run_local_async(scenario)


def test_deletion_and_return_require_a_new_processing_grant(consent_database_url: str) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-delete-return-{uuid4()}"
        )
        try:
            bot_id, actor, owner_id, old_authorization = await _seed_owner(
                sessions, accepted=True
            )
            assert old_authorization is not None
            async with sessions() as session, session.begin():
                assert await purge_owner(session, owner_id) >= 1
            async with sessions() as session:
                before_reacceptance = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                assert before_reacceptance.state == "required"
                assert not await authorize_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    authorization=old_authorization,
                )
            async with sessions() as session, session.begin():
                new_authorization = await seed_accepted_processing_consent(
                    session,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    telegram_user_id=actor,
                    expected_revision=before_reacceptance.revision,
                )
            assert new_authorization.grant_id != old_authorization.grant_id
            async with sessions() as session:
                assert not await authorize_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    authorization=old_authorization,
                )
                assert await authorize_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    authorization=new_authorization,
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_restore_invalidates_snapshot_grants_idempotently_before_release(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-restore-{uuid4()}"
        )
        sync_url = make_url(consent_database_url).set(drivername="postgresql+psycopg")
        sync_engine = create_sync_engine(sync_url)
        snapshot_id, ledger_id = "pin-20261018T000000Z", "pin-20261019T000000Z"
        try:
            bot_id, actor, owner_id, old_authorization = await _seed_owner(
                sessions, accepted=True
            )
            assert old_authorization is not None
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE platform.recovery_gate SET state='fenced', "
                        "snapshot_id=:snapshot, ledger_id=:ledger, "
                        "snapshot_at=:snapshot_at, fenced_at=:fenced, released_at=NULL, "
                        "consent_invalidated_snapshot_id=NULL, "
                        "consent_invalidated_ledger_id=NULL WHERE singleton=true"
                    ),
                    {
                        "snapshot": snapshot_id,
                        "ledger": ledger_id,
                        "snapshot_at": datetime(2026, 10, 18, tzinfo=UTC),
                        "fenced": datetime.now(UTC),
                    },
                )
            await _invalidate_restored_consents(
                consent_database_url,
                sync_engine,
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
            )
            async with sessions() as session:
                consent = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                gate = await session.get(RecoveryGate, True)
                assert consent.state == "required"
                assert consent.revision == 2
                assert gate is not None
                assert gate.consent_invalidated_snapshot_id == snapshot_id
                assert gate.consent_invalidated_ledger_id == ledger_id
                assert not await authorize_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    authorization=old_authorization,
                )
            await _invalidate_restored_consents(
                consent_database_url,
                sync_engine,
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
            )
            async with sessions() as session:
                current = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                assert current.revision == 2
            async with sessions() as session, session.begin():
                new_authorization = await seed_accepted_processing_consent(
                    session,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    telegram_user_id=actor,
                    expected_revision=2,
                )
            assert new_authorization.grant_id != old_authorization.grant_id
        finally:
            try:
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE platform.recovery_gate SET state='ready', snapshot_id=NULL, "
                            "ledger_id=NULL, snapshot_at=NULL, fenced_at=NULL, released_at=NULL, "
                            "deletion_evidence_sha256=NULL, delivery_evidence_sha256=NULL, "
                            "payment_evidence_sha256=NULL, usage_evidence_sha256=NULL, "
                            "consent_invalidated_snapshot_id=NULL, "
                            "consent_invalidated_ledger_id=NULL WHERE singleton=true"
                        )
                    )
            finally:
                sync_engine.dispose()
                await engine.dispose()

    run_local_async(scenario)


class _ReplyBarrierTransport:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        event_id: UUID,
        destination: int,
        sending: asyncio.Event,
    ) -> None:
        self.sessions = sessions
        self.event_id = event_id
        self.destination = destination
        self.sending = sending
        self.sent: list[TelegramReplySend] = []
        self.started_before_send: list[bool] = []

    async def resolve_destination(self, _session: AsyncSession, _owner_id: UUID) -> int:
        return self.destination

    async def send_reminder(self, _message: object) -> str:
        raise AssertionError("reply job must not send a reminder")

    async def send_reply(self, message: TelegramReplySend) -> str:
        self.sent.append(message)
        async with self.sessions() as session:
            attempt = await session.scalar(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.reply_event_id == self.event_id)
                .order_by(DeliveryAttemptRecord.attempt_number.desc())
            )
            self.started_before_send.append(attempt is not None and attempt.outcome == "started")
        self.sending.set()
        return "consent-barrier-reply"


def test_ai_reply_final_authorization_holds_withdrawal_until_attempt_commit(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        suffix = uuid4().hex[:12]
        worker_name = f"consent-reply-{suffix}"
        revoke_name = f"consent-reply-withdraw-{suffix}"
        engine, sessions = _factory(consent_database_url, application_name=worker_name)
        revoke_engine, revoke_sessions = _factory(
            consent_database_url, application_name=revoke_name
        )
        barrier_key = uuid4().int & ((1 << 62) - 1)
        function_name = f"consent_reply_wait_{suffix}"
        trigger_name = function_name
        function = f"notifications.{function_name}"
        holder = None
        delivery = None
        withdrawal = None
        try:
            bot_id, actor, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            event_id = uuid4()
            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
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
                        payload={
                            "chat_id": actor,
                            "markdown": "AI-derived reply",
                            "plain_text": "AI-derived reply",
                            "processing_classification": "ai_derived",
                            "processing_grant_id": str(authorization.grant_id),
                            "processing_policy_version": authorization.policy_version,
                        },
                    ),
                )
                event = await session.get(OutboxEvent, event_id)
                assert event is not None and event.created_at is not None
                assert await enqueue_due_reply_jobs(session, now=event.created_at) == 1
                claim = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id=f"consent-delivery-worker-{suffix}",
                    lease_for=timedelta(minutes=2),
                )
                assert claim is not None and claim.kind == DELIVERY_REPLY_JOB_KIND

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN IF NEW.owner_id = '" + str(owner_id) + "'::uuid THEN "
                        f"PERFORM pg_advisory_xact_lock({barrier_key}); "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger_name} BEFORE INSERT "
                        "ON notifications.delivery_attempts "
                        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
                    )
                )
            holder = await engine.connect()
            await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": barrier_key})
            sending = asyncio.Event()
            transport = _ReplyBarrierTransport(sessions, event_id, actor, sending)
            delivery = asyncio.create_task(
                deliver_reply_job(
                    sessions,
                    claim,
                    transport,
                    now=lambda: datetime.now(UTC),
                )
            )
            await _wait_for_activity_or_task_failure(
                engine, worker_name, "advisory", delivery
            )
            withdrawal = asyncio.create_task(
                _withdraw(revoke_sessions, owner_id, expected_grant=authorization.grant_id)
            )
            await _wait_for_blocker(revoke_engine, revoke_name, worker_name)
            assert not withdrawal.done() and not delivery.done()

            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key})
            await holder.commit()
            outcome = await asyncio.wait_for(delivery, timeout=10)
            await asyncio.wait_for(withdrawal, timeout=10)
            assert outcome is not None and outcome.outcome.value == "confirmed"
            assert sending.is_set()
            assert len(transport.sent) == 1
            assert transport.started_before_send == [True]
            async with sessions() as session:
                consent = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.reply_event_id == event_id
                    )
                )
            assert consent.state == "revoked"
            assert attempt is not None and attempt.outcome == "confirmed"
        finally:
            for task in (delivery, withdrawal):
                if task is not None and not task.done():
                    task.cancel()
            if delivery is not None or withdrawal is not None:
                await asyncio.gather(
                    *(task for task in (delivery, withdrawal) if task is not None),
                    return_exceptions=True,
                )
            if holder is not None:
                try:
                    await holder.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key}
                    )
                    await holder.commit()
                except Exception:
                    pass
                await holder.close()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"DROP TRIGGER IF EXISTS {trigger_name} "
                            "ON notifications.delivery_attempts"
                        )
                    )
                    await connection.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
            finally:
                await engine.dispose()
                await revoke_engine.dispose()

    run_local_async(scenario)


def test_withdrawal_before_ai_reply_attempt_denies_transport(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        engine, sessions = _factory(
            consent_database_url, application_name=f"consent-reply-denied-{uuid4()}"
        )
        try:
            _, actor, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            event_id = uuid4()
            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
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
                        payload={
                            "chat_id": actor,
                            "markdown": "AI-derived reply",
                            "plain_text": "AI-derived reply",
                            "processing_classification": "ai_derived",
                            "processing_grant_id": str(authorization.grant_id),
                            "processing_policy_version": authorization.policy_version,
                        },
                    ),
                )
                event = await session.get(OutboxEvent, event_id)
                assert event is not None and event.created_at is not None
                assert await enqueue_due_reply_jobs(session, now=event.created_at) == 1
                claim = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id=f"consent-delivery-denied-{uuid4()}",
                    lease_for=timedelta(minutes=2),
                )
                assert claim is not None and claim.kind == DELIVERY_REPLY_JOB_KIND
            await _withdraw(sessions, owner_id, expected_grant=authorization.grant_id)
            transport = _ReplyBarrierTransport(sessions, event_id, actor, asyncio.Event())
            outcome = await deliver_reply_job(
                sessions, claim, transport, now=lambda: datetime.now(UTC)
            )
            assert outcome is not None and outcome.failure_code == "processing_consent_stale"
            assert transport.sent == []
            async with sessions() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(DeliveryAttemptRecord).where(
                                DeliveryAttemptRecord.reply_event_id == event_id
                            )
                        )
                    ).all()
                )
            assert len(attempts) == 1 and attempts[0].outcome == "skipped"
        finally:
            await engine.dispose()

    run_local_async(scenario)


class _BlockedValidReply:
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release
        self.calls = 0

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {
            "id": f"consent-capture-{uuid4()}",
            "model": ROUTE,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"kind": "reply", "language": "en", "text": "I saved that."}
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost": "0.000001",
            },
        }


class _BlockedValidProposal(_BlockedValidReply):
    def __init__(self, entered: asyncio.Event, release: asyncio.Event, *, due_date: date) -> None:
        super().__init__(entered, release)
        self.due_date = due_date

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return {
            "id": f"consent-capture-{uuid4()}",
            "model": ROUTE,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "kind": "proposal",
                                "language": "en",
                                "operation": "propose_create_plan",
                                "fields": {
                                    "title": "Review the consent result",
                                    "schedule": {
                                        "kind": "task",
                                        "local_date": self.due_date.isoformat(),
                                        "local_time": "09:00:00",
                                        "timezone": "UTC",
                                        "timing": "timed",
                                    },
                                    "reminders": [],
                                    "reminder_mode": "override",
                                },
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost": "0.000001",
            },
        }


def test_capture_result_and_confirmation_proofs_commit_before_withdrawal(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        suffix = uuid4().hex[:12]
        app_name, revoke_name = f"consent-result-{suffix}", f"consent-result-withdraw-{suffix}"
        engine, sessions = _factory(consent_database_url, application_name=app_name)
        revoke_engine, revoke_sessions = _factory(
            consent_database_url, application_name=revoke_name
        )
        entered, release = asyncio.Event(), asyncio.Event()
        barrier_key = uuid4().int & ((1 << 62) - 1)
        function_name = f"consent_proof_wait_{suffix}"
        function, trigger = f"conversation.{function_name}", function_name
        holder = capture = withdrawal = None
        try:
            bot_id, actor, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=f"consent-capture-{uuid4().hex}",
                        policy_kind="trial",
                        policy_version="test",
                        starts_at=now - timedelta(minutes=1),
                        ends_at=now + timedelta(days=2),
                        ai_turn_limit=5,
                        voice_second_limit=60,
                    )
                )
                session.add(
                    PreferencesRecord(
                        owner_id=owner_id,
                        language="en",
                        home_zone="UTC",
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
                update_id = uuid4().int % 9_000_000_000_000_000
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=_message(update_id, actor),
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
                job = await session.scalar(
                    select(Job).where(
                        Job.deduplication_key == f"telegram:{bot_id}:{update_id}"
                    )
                )
                assert job is not None
                await session.execute(
                    update(Job).where(Job.id == job.id).values(priority=2_147_483_647)
                )
            claim = await claim_application_job(sessions, worker_id=f"consent-result-{suffix}")
            assert claim is not None and claim.job_id == job.id

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN IF NEW.owner_id = '" + str(owner_id) + "'::uuid THEN "
                        f"PERFORM pg_advisory_xact_lock({barrier_key}); "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger} BEFORE INSERT "
                        "ON conversation.capture_confirmations "
                        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
                    )
                )
            holder = await engine.connect()
            await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": barrier_key})
            assistant = BoundedPlanningAssistant(
                transport=_BlockedValidProposal(
                    entered, release, due_date=now.date() + timedelta(days=2)
                ),
                usage=SqlAlchemyProviderUsageRecorder(sessions),
                allowance=SqlAlchemyTurnAllowance(sessions),
                now=lambda: now,
            )
            capture = asyncio.create_task(
                process_application_job(sessions, claim, now=now, assistant=assistant)
            )
            await asyncio.wait_for(entered.wait(), timeout=10)
            release.set()
            await _wait_for_activity_or_task_failure(engine, app_name, "advisory", capture)
            withdrawal = asyncio.create_task(
                _withdraw(revoke_sessions, owner_id, expected_grant=authorization.grant_id)
            )
            await _wait_for_blocker(revoke_engine, revoke_name, app_name)
            assert not withdrawal.done() and not capture.done()

            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key})
            await holder.commit()
            await asyncio.wait_for(capture, timeout=10)
            await asyncio.wait_for(withdrawal, timeout=10)
            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
                )
                proofs = list(
                    (
                        await session.scalars(
                            select(CaptureConfirmationRecord).where(
                                CaptureConfirmationRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                consent = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
            assert consent.state == "revoked"
            assert draft is not None and draft.state == "ready"
            assert draft.processing_grant_id == authorization.grant_id
            assert len(proofs) == 1 and proofs[0].consumed_at is None
            assert draft.preview is not None and draft.preview.get("proofs")
        finally:
            release.set()
            for task in (capture, withdrawal):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (capture, withdrawal) if task is not None),
                return_exceptions=True,
            )
            if holder is not None:
                try:
                    await holder.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key}
                    )
                    await holder.commit()
                except Exception:
                    pass
                await holder.close()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"DROP TRIGGER IF EXISTS {trigger} "
                            "ON conversation.capture_confirmations"
                        )
                    )
                    await connection.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
            finally:
                await engine.dispose()
                await revoke_engine.dispose()

    run_local_async(scenario)


def test_capture_worker_discards_and_terminalizes_result_when_withdrawal_wins(
    consent_database_url: str,
) -> None:
    async def scenario() -> None:
        app_name = f"consent-capture-{uuid4()}"
        engine, sessions = _factory(consent_database_url, application_name=app_name)
        entered, release = asyncio.Event(), asyncio.Event()
        capture = None
        try:
            bot_id, actor, owner_id, authorization = await _seed_owner(sessions, accepted=True)
            assert authorization is not None
            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=f"consent-capture-{uuid4().hex}",
                        policy_kind="trial",
                        policy_version="test",
                        starts_at=now - timedelta(minutes=1),
                        ends_at=now + timedelta(days=2),
                        ai_turn_limit=5,
                        voice_second_limit=60,
                    )
                )
                session.add(
                    PreferencesRecord(
                        owner_id=owner_id,
                        language="en",
                        home_zone="UTC",
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
                update_id = uuid4().int % 9_000_000_000_000_000
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=_message(update_id, actor),
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
                assert receipt.accepted
                job = await session.scalar(
                    select(Job).where(
                        Job.deduplication_key == f"telegram:{bot_id}:{update_id}"
                    )
                )
                assert job is not None
                await session.execute(
                    update(Job)
                    .where(Job.id == job.id)
                    .values(priority=2_147_483_647)
                )
            claim = await claim_application_job(sessions, worker_id=f"consent-capture-{uuid4()}")
            assert claim is not None and claim.job_id == job.id
            assert claim.owner_id == owner_id
            transport = _BlockedValidReply(entered, release)
            assistant = BoundedPlanningAssistant(
                transport=transport,
                usage=SqlAlchemyProviderUsageRecorder(sessions),
                allowance=SqlAlchemyTurnAllowance(sessions),
                now=lambda: now,
            )
            capture = asyncio.create_task(
                process_application_job(sessions, claim, now=now, assistant=assistant)
            )
            async with asyncio.timeout(10):
                await entered.wait()
            await _withdraw(sessions, owner_id, expected_grant=authorization.grant_id)
            release.set()
            await asyncio.wait_for(capture, timeout=10)
            assert transport.calls == 1
            async with sessions() as session:
                draft = await session.scalar(
                    select(CaptureDraftRecord).where(CaptureDraftRecord.owner_id == owner_id)
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
                replies = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.owner_id == owner_id,
                                OutboxEvent.event_type == "telegram.reply.requested",
                            )
                        )
                    ).all()
                )
                assert draft is not None
                assert draft.state == "failed"
                assert draft.preview is None and draft.processing_key is None
                assert len(calls) == 1 and calls[0].state == "reported"
                assert calls[0].cost_usd == Decimal("0.000001000000")
                assert len(replies) == 1
                payload = replies[0].payload["payload"]
                assert payload["processing_classification"] == "deterministic"
                assert payload.get("processing_grant_id") is None
                assert "I saved that." not in str(payload)
        finally:
            release.set()
            if capture is not None and not capture.done():
                capture.cancel()
                await asyncio.gather(capture, return_exceptions=True)
            await engine.dispose()

    run_local_async(scenario)
