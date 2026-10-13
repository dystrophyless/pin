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
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy import func, make_url, select, text

from pin.contexts.billing.api import (
    AccessDenial,
    AccountLifecycle,
    CheckAccess,
    InvalidTransition,
    Operation,
    PeriodKind,
    QuotaExceeded,
    ReservationState,
    ReserveUsage,
    ResizeVoiceUsage,
    SqlAlchemyAccessUsageStore,
    StartTrial,
    TrialActivationStatus,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    TrialActivationRecord,
    UsageReservationRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def billing_database() -> Iterator[str]:
    database_url = os.getenv("PIN_TEST_BILLING_DATABASE_URL") or os.getenv(
        "PIN_TEST_PLATFORM_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("set PIN_TEST_BILLING_DATABASE_URL to a disposable PostgreSQL database")
    url = make_url(database_url)
    if (
        url.host != "127.0.0.1"
        or url.port != 51017
        or not (url.database or "").startswith("pin_issue6_")
    ):
        raise RuntimeError("billing tests require the designated pin_issue6_ test database")
    guard_engine = sync_create_engine(database_url)
    try:
        with guard_engine.connect() as connection:
            data_directory = connection.scalar(text("SHOW data_directory"))
            database_name = connection.scalar(text("SELECT current_database()"))
        normalized_data_directory = str(data_directory).replace("\\", "/").rstrip("/").casefold()
        if (
            normalized_data_directory
            != "c:/users/roman/appdata/local/temp/pin-issue2-eeadfd1-20261013"
            or database_name != url.database
        ):
            raise RuntimeError("billing tests connected outside the designated disposable cluster")
    finally:
        guard_engine.dispose()
    command.upgrade(_alembic_config(database_url), "head")
    yield database_url


def _seed_period(
    session,
    *,
    owner_id,
    period_id: str,
    now: datetime,
    ai_limit: int = 250,
    voice_limit: int = 900,
) -> tuple[datetime, datetime]:
    starts_at = now - timedelta(seconds=1)
    ends_at = starts_at + timedelta(days=7)
    session.add(AccessAccountRecord(owner_id=owner_id))
    session.add(
        AccessPeriodRecord(
            owner_id=owner_id,
            period_id=period_id,
            policy_kind=PeriodKind.TRIAL.value,
            policy_version="trial-v1",
            starts_at=starts_at,
            ends_at=ends_at,
            ai_turn_limit=ai_limit,
            voice_second_limit=voice_limit,
        )
    )
    return starts_at, ends_at


def test_account_lifecycle_distinguishes_missing_present_and_tombstoned(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        missing_owner, expired_owner, deleted_owner = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=expired_owner))
                session.add(
                    AccessPeriodRecord(
                        owner_id=expired_owner,
                        period_id="expired-lifecycle-check",
                        policy_kind=PeriodKind.TRIAL.value,
                        policy_version="trial-v1",
                        starts_at=now - timedelta(days=8),
                        ends_at=now - timedelta(days=1),
                        ai_turn_limit=250,
                        voice_second_limit=900,
                    )
                )
                session.add(AccessAccountRecord(owner_id=deleted_owner, deleted_at=now))

            async with sessions() as session:
                store = SqlAlchemyAccessUsageStore(session)
                assert await store.account_lifecycle(missing_owner) is AccountLifecycle.MISSING
                assert await store.account_lifecycle(expired_owner) is AccountLifecycle.PRESENT
                assert await store.account_lifecycle(deleted_owner) is AccountLifecycle.DELETED

                missing_access = await store.check_access(
                    CheckAccess(missing_owner, Operation.VIEW_PLAN, now)
                )
                expired_access = await store.check_access(
                    CheckAccess(expired_owner, Operation.AI_TURN, now)
                )
                deleted_access = await store.check_access(
                    CheckAccess(deleted_owner, Operation.VIEW_PLAN, now)
                )
                assert missing_access.denial is AccessDenial.ACCOUNT_DELETED
                assert expired_access.denial is AccessDenial.ACCESS_EXPIRED
                assert deleted_access.denial is AccessDenial.ACCOUNT_DELETED
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_voice_resize_reads_are_owner_scoped_and_preserve_original_period(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, expired_owner = uuid4(), uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=5)
        try:
            async with sessions() as session, session.begin():
                _, period_end = _seed_period(
                    session,
                    owner_id=owner_id,
                    period_id="original-trial",
                    now=now,
                    ai_limit=2,
                    voice_limit=100,
                )
                result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="prepared-voice",
                        at=now,
                        expires_at=expires_at,
                        ai_turns=0,
                        voice_seconds=20,
                        operation=Operation.VOICE_TRANSCRIPTION,
                    )
                )
                assert result.created and result.reservation is not None
                # A newer entitlement must not move the hold away from its trial period.
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id="current-paid",
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=now - timedelta(seconds=1),
                        ends_at=now + timedelta(days=30),
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )
                _seed_period(
                    session,
                    owner_id=expired_owner,
                    period_id="expired-hold",
                    now=now,
                    ai_limit=2,
                    voice_limit=100,
                )
                assert period_end > expires_at
                expired = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=expired_owner,
                        request_id="expiring-voice",
                        at=now,
                        expires_at=now + timedelta(hours=1),
                        ai_turns=0,
                        voice_seconds=20,
                        operation=Operation.VOICE_TRANSCRIPTION,
                    )
                )
                assert expired.created and expired.reservation is not None

            async with sessions() as reader:
                reader_store = SqlAlchemyAccessUsageStore(reader)
                before = await reader_store.get_usage_reservation(owner_id, "prepared-voice")
                assert before is not None and before.period_id == "original-trial"
                async with sessions() as writer, writer.begin():
                    resized = await SqlAlchemyAccessUsageStore(writer).resize_voice_usage(
                        ResizeVoiceUsage(owner_id, "prepared-voice", 60, now + timedelta(seconds=1))
                    )
                    assert resized.period_id == "original-trial"
                    assert resized.voice_seconds == 60 and resized.ai_turns == 0
                refreshed = await reader_store.get_usage_reservation(owner_id, "prepared-voice")
                assert refreshed == resized
                assert await reader_store.get_usage_reservation(uuid4(), "prepared-voice") is None
                assert await reader_store.get_usage_reservation(owner_id, "unknown") is None

            async with sessions() as session, session.begin():
                with pytest.raises(QuotaExceeded):
                    await SqlAlchemyAccessUsageStore(session).resize_voice_usage(
                        ResizeVoiceUsage(
                            owner_id, "prepared-voice", 101, now + timedelta(seconds=2)
                        )
                    )
            async with sessions() as session:
                stored = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(
                    owner_id, "prepared-voice"
                )
                assert stored == resized
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageReservationRecord)
                        .where(UsageReservationRecord.owner_id == owner_id)
                    )
                    == 1
                )

            async with sessions() as session, session.begin():
                account = await session.get(AccessAccountRecord, owner_id)
                assert account is not None
                account.deleted_at = now + timedelta(seconds=3)
            async with sessions() as session, session.begin():
                with pytest.raises(InvalidTransition, match="entitlement"):
                    await SqlAlchemyAccessUsageStore(session).resize_voice_usage(
                        ResizeVoiceUsage(owner_id, "prepared-voice", 50, now + timedelta(seconds=4))
                    )
                with pytest.raises(InvalidTransition):
                    await SqlAlchemyAccessUsageStore(session).resize_voice_usage(
                        ResizeVoiceUsage(
                            expired_owner, "expiring-voice", 30, now + timedelta(hours=1)
                        )
                    )
            async with sessions() as session:
                unchanged = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(
                    owner_id, "prepared-voice"
                )
                expired = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(
                    expired_owner, "expiring-voice"
                )
                assert unchanged == resized
                assert expired is not None and expired.voice_seconds == 20
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_concurrent_voice_resize_and_reservation_cannot_overspend(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, now = uuid4(), datetime.now(UTC)
        expires_at = now + timedelta(minutes=5)
        try:
            async with sessions() as session, session.begin():
                _seed_period(
                    session,
                    owner_id=owner_id,
                    period_id="voice-capacity",
                    now=now,
                    ai_limit=2,
                    voice_limit=100,
                )
                initial = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="top-up",
                        at=now,
                        expires_at=expires_at,
                        ai_turns=0,
                        voice_seconds=20,
                        operation=Operation.VOICE_TRANSCRIPTION,
                    )
                )
                assert initial.created and initial.reservation is not None

            async def resize() -> bool:
                try:
                    async with sessions() as session, session.begin():
                        await SqlAlchemyAccessUsageStore(session).resize_voice_usage(
                            ResizeVoiceUsage(owner_id, "top-up", 70, now + timedelta(seconds=1))
                        )
                    return True
                except QuotaExceeded:
                    return False

            async def reserve() -> bool:
                try:
                    async with sessions() as session, session.begin():
                        result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                            _reservation_command(
                                owner_id=owner_id,
                                request_id="competitor",
                                at=now + timedelta(seconds=1),
                                expires_at=expires_at,
                                ai_turns=0,
                                voice_seconds=40,
                                operation=Operation.VOICE_TRANSCRIPTION,
                            )
                        )
                        assert result.created and result.reservation is not None
                    return True
                except QuotaExceeded:
                    return False

            outcomes = await asyncio.gather(resize(), reserve())
            assert sorted(outcomes) == [False, True]
            async with sessions() as session:
                store = SqlAlchemyAccessUsageStore(session)
                reservations = [
                    await store.get_usage_reservation(owner_id, request_id)
                    for request_id in ("top-up", "competitor")
                ]
                total_reserved = sum(
                    item.voice_seconds
                    for item in reservations
                    if item is not None and item.state is ReservationState.RESERVED
                )
                rows = await session.scalar(
                    select(func.count())
                    .select_from(UsageReservationRecord)
                    .where(UsageReservationRecord.owner_id == owner_id)
                )
                assert total_reserved <= 100
                assert rows == 2 if outcomes[1] else rows == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_trial_activation_is_owner_scoped_and_replay_safe(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, now, telegram_id = uuid4(), datetime.now(UTC), uuid4().int % 2**52
        try:
            async with sessions() as session, session.begin():
                first = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now)
                )
                assert first.status is TrialActivationStatus.ACTIVATED
                assert first.starts_at == now
                assert first.ends_at == now + timedelta(days=7)
            async with sessions() as session, session.begin():
                replay = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now + timedelta(days=20))
                )
                assert replay.status is TrialActivationStatus.PREVIOUSLY_ACTIVATED
                assert replay.period_id == first.period_id
            async with sessions() as session, session.begin():
                with pytest.raises(InvalidTransition, match="prior trial identity"):
                    await SqlAlchemyAccessUsageStore(session).activate_trial(
                        StartTrial(owner_id, telegram_id + 1, now + timedelta(days=21))
                    )
            async with sessions() as session:
                rows = list(
                    (await session.scalars(
                        select(AccessPeriodRecord).where(
                            AccessPeriodRecord.owner_id == owner_id,
                            AccessPeriodRecord.policy_kind == PeriodKind.TRIAL.value,
                        )
                    )).all()
                )
                assert len(rows) == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_concurrent_trial_activation_creates_one_period(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, now = uuid4(), datetime.now(UTC)
        telegram_id = uuid4().int % 2**52
        try:
            async def activate() -> TrialActivationStatus:
                async with sessions() as session, session.begin():
                    result = await SqlAlchemyAccessUsageStore(session).activate_trial(
                        StartTrial(owner_id, telegram_id, now)
                    )
                    return result.status

            assert sorted(await asyncio.gather(activate(), activate())) == sorted(
                (TrialActivationStatus.ACTIVATED, TrialActivationStatus.PREVIOUSLY_ACTIVATED)
            )
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count()).select_from(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id,
                        AccessPeriodRecord.policy_kind == PeriodKind.TRIAL.value,
                    )
                )
                assert count == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_trial_activation_preserves_active_paid_period(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, now = uuid4(), datetime.now(UTC)
        telegram_id = uuid4().int % 2**52
        try:
            async with sessions() as session, session.begin():
                paid_start = now - timedelta(days=1)
                paid_end = paid_start + timedelta(days=30)
                session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=now))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id="paid-current",
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=paid_start,
                        ends_at=paid_end,
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )
                session.add(
                    UsageReservationRecord(
                        owner_id=owner_id,
                        period_id="paid-current",
                        request_id="paid-usage-preserved",
                        reserved_ai_turns=1,
                        reserved_voice_seconds=0,
                        settled_ai_turns=1,
                        settled_voice_seconds=0,
                        state=ReservationState.SETTLED.value,
                        created_at=now,
                        expires_at=paid_end,
                        finalized_at=now,
                    )
                )
            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now)
                )
                assert result.status is TrialActivationStatus.ALREADY_ACTIVE
                assert result.period_id == "paid-current"
                assert result.starts_at == paid_start and result.ends_at == paid_end
            async with sessions() as session:
                account = await session.get(AccessAccountRecord, owner_id)
                assert account is not None and account.deleted_at is None
                count = await session.scalar(
                    select(func.count()).select_from(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id
                    )
                )
                assert count == 1
                usage = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == "paid-usage-preserved",
                    )
                )
                assert usage is not None and usage.settled_ai_turns == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_trial_activation_shares_caller_transaction_and_survives_return(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, now, telegram_id = uuid4(), datetime.now(UTC), uuid4().int % 2**52
        try:
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
            with pytest.raises(RuntimeError, match="rollback onboarding"):
                async with sessions() as session, session.begin():
                    await SqlAlchemyAccessUsageStore(session).activate_trial(
                        StartTrial(owner_id, telegram_id, now)
                    )
                    raise RuntimeError("rollback onboarding")
            async with sessions() as session:
                assert await session.scalar(
                    select(func.count()).select_from(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id
                    )
                ) == 0

            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now)
                )
                assert result.status is TrialActivationStatus.ACTIVATED
            async with sessions() as session, session.begin():
                await session.delete(await session.get(AccessAccountRecord, owner_id))
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id))
            async with sessions() as session, session.begin():
                returned = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now + timedelta(days=30))
                )
                assert returned.status is TrialActivationStatus.PREVIOUSLY_ACTIVATED
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_return_after_expired_paid_period_restores_account_only(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, telegram_id = uuid4(), uuid4().int % 2**52
        now = datetime.now(UTC)
        paid_start = now - timedelta(days=40)
        paid_end = paid_start + timedelta(days=30)
        try:
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=now))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id="expired-paid",
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=paid_start,
                        ends_at=paid_end,
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )
                session.add(
                    TrialActivationRecord(
                        owner_id=owner_id,
                        telegram_user_id=telegram_id,
                        period_id="old-trial",
                        activated_at=paid_start - timedelta(days=10),
                    )
                )
            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now)
                )
                assert result.status is TrialActivationStatus.PREVIOUSLY_ACTIVATED
            async with sessions() as session:
                account = await session.get(AccessAccountRecord, owner_id)
                assert account is not None and account.deleted_at is None
                store = SqlAlchemyAccessUsageStore(session)
                for operation in (
                    Operation.VIEW_PLAN,
                    Operation.COMPLETE_PLAN,
                    Operation.CANCEL_PLAN,
                ):
                    assert (await store.check_access(CheckAccess(owner_id, operation, now))).allowed
                assert not (await store.check_access(
                    CheckAccess(owner_id, Operation.AI_TURN, now)
                )).allowed
                assert await session.scalar(
                    select(func.count()).select_from(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == owner_id
                    )
                ) == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_return_does_not_restore_deleted_active_trial_coverage(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, telegram_id = uuid4(), uuid4().int % 2**52
        now = datetime.now(UTC)
        starts_at, ends_at = now - timedelta(days=1), now + timedelta(days=6)
        try:
            async with sessions() as session, session.begin():
                session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=now))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id="deleted-trial",
                        policy_kind=PeriodKind.TRIAL.value,
                        policy_version="v1",
                        starts_at=starts_at,
                        ends_at=ends_at,
                        ai_turn_limit=250,
                        voice_second_limit=900,
                    )
                )
                session.add(
                    TrialActivationRecord(
                        owner_id=owner_id,
                        telegram_user_id=telegram_id,
                        period_id="deleted-trial",
                        activated_at=starts_at,
                    )
                )
            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).activate_trial(
                    StartTrial(owner_id, telegram_id, now)
                )
                assert result.status is TrialActivationStatus.PREVIOUSLY_ACTIVATED
            async with sessions() as session:
                store = SqlAlchemyAccessUsageStore(session)
                assert (await store.check_access(
                    CheckAccess(owner_id, Operation.VIEW_PLAN, now)
                )).allowed
                assert not (await store.check_access(
                    CheckAccess(owner_id, Operation.AI_TURN, now)
                )).allowed
                period = await session.get(AccessPeriodRecord, (owner_id, "deleted-trial"))
                assert period is not None and period.ends_at == ends_at
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_check_access_refreshes_account_after_concurrent_deletion(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        try:
            async with sessions() as writer, writer.begin():
                _seed_period(writer, owner_id=owner_id, period_id=period_id, now=now)

            async with sessions() as reader:
                store = SqlAlchemyAccessUsageStore(reader)
                query = CheckAccess(owner_id, Operation.AI_TURN, now)
                assert (await store.check_access(query)).allowed
                async with sessions() as writer, writer.begin():
                    account = await writer.get(AccessAccountRecord, owner_id)
                    assert account is not None
                    account.deleted_at = now
                decision = await store.check_access(query)
                assert not decision.allowed
                assert decision.denial is AccessDenial.ACCOUNT_DELETED
        finally:
            await engine.dispose()

    run_local_async(scenario)


def _reservation_command(
    *,
    owner_id,
    request_id: str,
    at: datetime,
    expires_at: datetime,
    ai_turns: int,
    voice_seconds: int,
    operation: Operation = Operation.AI_TURN,
) -> ReserveUsage:
    return ReserveUsage(
        owner_id=owner_id,
        request_id=request_id,
        operation=operation,
        ai_turns=ai_turns,
        voice_seconds=voice_seconds,
        at=at,
        expires_at=expires_at,
    )


def test_concurrent_final_turn_reservations_have_one_winner(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                _, ends_at = _seed_period(session, owner_id=owner_id, period_id=period_id, now=now)
                session.add_all(
                    UsageReservationRecord(
                        owner_id=owner_id,
                        period_id=period_id,
                        request_id=f"used-{index}",
                        reserved_ai_turns=1,
                        reserved_voice_seconds=0,
                        settled_ai_turns=1,
                        settled_voice_seconds=0,
                        state=ReservationState.SETTLED.value,
                        created_at=now,
                        expires_at=ends_at,
                        finalized_at=now,
                    )
                    for index in range(249)
                )

            async def attempt(index: int) -> bool:
                try:
                    async with sessions() as session, session.begin():
                        result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                            _reservation_command(
                                owner_id=owner_id,
                                request_id=f"last-{index}",
                                at=now,
                                expires_at=ends_at,
                                ai_turns=1,
                                voice_seconds=0,
                            )
                        )
                        assert result.access.allowed and result.reservation is not None
                        assert result.created
                    return True
                except QuotaExceeded:
                    return False

            outcomes = await asyncio.gather(attempt(1), attempt(2))
            assert sorted(outcomes) == [False, True]
            async with sessions() as session:
                total = await session.scalar(
                    select(func.count())
                    .select_from(UsageReservationRecord)
                    .where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.state == ReservationState.RESERVED.value,
                    )
                )
                assert total == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_concurrent_same_key_reports_one_creation_and_one_replay(billing_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                _, ends_at = _seed_period(session, owner_id=owner_id, period_id=period_id, now=now)

            async def attempt() -> bool:
                async with sessions() as session, session.begin():
                    result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                        _reservation_command(
                            owner_id=owner_id,
                            request_id="same-key",
                            at=now,
                            expires_at=ends_at,
                            ai_turns=1,
                            voice_seconds=0,
                        )
                    )
                    assert result.reservation is not None
                    return result.created

            assert sorted(await asyncio.gather(attempt(), attempt())) == [False, True]
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count()).select_from(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id
                    )
                )
                assert count == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_dual_quota_reservation_and_duplicate_settlement_stay_in_original_period(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                _, ends_at = _seed_period(session, owner_id=owner_id, period_id=period_id, now=now)
                session.add_all(
                    UsageReservationRecord(
                        owner_id=owner_id,
                        period_id=period_id,
                        request_id=f"used-{index}",
                        reserved_ai_turns=1,
                        reserved_voice_seconds=155 if index == 0 else 3,
                        settled_ai_turns=1,
                        settled_voice_seconds=155 if index == 0 else 3,
                        state=ReservationState.SETTLED.value,
                        created_at=now,
                        expires_at=ends_at,
                        finalized_at=now,
                    )
                    for index in range(249)
                )

            request = _reservation_command(
                owner_id=owner_id,
                request_id="combined-voice-turn",
                at=now,
                expires_at=ends_at,
                ai_turns=1,
                voice_seconds=1,
                operation=Operation.VOICE_TRANSCRIPTION,
            )
            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).reserve_usage(request)
                assert result.access.period_id == period_id
                assert result.reservation is not None
                assert result.reservation.period_id == period_id

            for retry in (
                _reservation_command(
                    owner_id=owner_id,
                    request_id="would-overrun-turns",
                    at=now,
                    expires_at=ends_at,
                    ai_turns=1,
                    voice_seconds=0,
                ),
                _reservation_command(
                    owner_id=owner_id,
                    request_id="would-overrun-seconds",
                    at=now,
                    expires_at=ends_at,
                    ai_turns=0,
                    voice_seconds=1,
                    operation=Operation.VOICE_TRANSCRIPTION,
                ),
            ):
                async with sessions() as session, session.begin():
                    with pytest.raises(QuotaExceeded):
                        await SqlAlchemyAccessUsageStore(session).reserve_usage(retry)

            async with sessions() as session, session.begin():
                store = SqlAlchemyAccessUsageStore(session)
                settled = await store.settle_usage(
                    owner_id=owner_id,
                    request_id="combined-voice-turn",
                    ai_turns=1,
                    voice_seconds=1,
                    at=now + timedelta(seconds=1),
                )
                replay = await store.settle_usage(
                    owner_id=owner_id,
                    request_id="combined-voice-turn",
                    ai_turns=1,
                    voice_seconds=1,
                    at=now + timedelta(seconds=2),
                )
                assert settled == replay
                assert settled.period_id == period_id
            async with sessions() as session:
                stored = await session.scalar(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id,
                        UsageReservationRecord.request_id == "combined-voice-turn",
                    )
                )
                assert stored is not None
                assert stored.state == ReservationState.SETTLED.value
                assert stored.settled_ai_turns == 1 and stored.settled_voice_seconds == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_paid_period_gets_fresh_quota_without_changing_trial_ledger(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, trial_id, paid_id = uuid4(), str(uuid4()), str(uuid4())
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                starts_at, trial_ends_at = _seed_period(
                    session,
                    owner_id=owner_id,
                    period_id=trial_id,
                    now=now - timedelta(days=1),
                )
                session.add(
                    UsageReservationRecord(
                        owner_id=owner_id,
                        period_id=trial_id,
                        request_id="trial-cost",
                        reserved_ai_turns=1,
                        reserved_voice_seconds=0,
                        settled_ai_turns=1,
                        settled_voice_seconds=0,
                        state=ReservationState.SETTLED.value,
                        created_at=starts_at,
                        expires_at=trial_ends_at,
                        finalized_at=now,
                    )
                )
                paid_starts = now - timedelta(seconds=1)
                paid_ends = paid_starts + timedelta(days=30)
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=paid_id,
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=paid_starts,
                        ends_at=paid_ends,
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )

            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="paid-cost",
                        at=now,
                        expires_at=paid_ends,
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
                assert result.access.period_id == paid_id
                assert result.reservation is not None
                assert result.reservation.period_id == paid_id

            later_paid_id = str(uuid4())
            async with sessions() as session, session.begin():
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=later_paid_id,
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=now + timedelta(days=1),
                        ends_at=now + timedelta(days=31),
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )

            async with sessions() as session, session.begin():
                replay = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="paid-cost",
                        at=paid_ends + timedelta(days=1),
                        expires_at=paid_ends,
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
                assert replay.access.period_id == later_paid_id
                assert replay.reservation is not None
                assert replay.reservation.period_id == paid_id
                assert replay.reservation.request_id == "paid-cost"
                assert replay.reservation.state == ReservationState.EXPIRED

            async with sessions() as session:
                records = list(
                    (
                        await session.scalars(
                            select(UsageReservationRecord)
                            .where(UsageReservationRecord.owner_id == owner_id)
                            .order_by(UsageReservationRecord.period_id)
                        )
                    ).all()
                )
                old_period = next(record for record in records if record.request_id == "trial-cost")
                new_period = next(record for record in records if record.request_id == "paid-cost")
                assert old_period.period_id == trial_id
                assert old_period.state == ReservationState.SETTLED.value
                assert old_period.settled_ai_turns == 1
                assert new_period.period_id == paid_id
                assert new_period.state == ReservationState.EXPIRED.value
                assert len(records) == 2
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_refund_and_expiry_release_reservations_and_fence_late_work(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        expiry = now + timedelta(minutes=2)
        try:
            async with sessions() as session, session.begin():
                _, ends_at = _seed_period(session, owner_id=owner_id, period_id=period_id, now=now)
            async with sessions() as session, session.begin():
                await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="failed-request",
                        at=now,
                        expires_at=expiry,
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
            async with sessions() as session, session.begin():
                store = SqlAlchemyAccessUsageStore(session)
                first = await store.refund_usage(
                    owner_id=owner_id, request_id="failed-request", at=now + timedelta(seconds=1)
                )
                replay = await store.refund_usage(
                    owner_id=owner_id, request_id="failed-request", at=now + timedelta(seconds=2)
                )
                assert first == replay and first.state is ReservationState.REFUNDED

            async with sessions() as session, session.begin():
                await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="crashed-request",
                        at=now + timedelta(seconds=3),
                        expires_at=expiry,
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
            async with sessions() as session, session.begin():
                count = await SqlAlchemyAccessUsageStore(session).expire_reservations(
                    at=expiry, limit=1000
                )
                assert 1 <= count <= 1000
            async with sessions() as session:
                expired = await SqlAlchemyAccessUsageStore(session).get_usage_reservation(
                    owner_id, "crashed-request"
                )
                assert expired is not None and expired.state is ReservationState.EXPIRED
            async with sessions() as session, session.begin():
                with pytest.raises(InvalidTransition):
                    await SqlAlchemyAccessUsageStore(session).settle_usage(
                        owner_id=owner_id,
                        request_id="crashed-request",
                        ai_turns=1,
                        voice_seconds=0,
                        at=expiry,
                    )
            async with sessions() as session:
                decision = await SqlAlchemyAccessUsageStore(session).check_access(
                    CheckAccess(owner_id=owner_id, operation=Operation.AI_TURN, at=ends_at)
                )
                assert not decision.allowed and decision.denial is AccessDenial.ACCESS_EXPIRED
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_reservation_is_owner_scoped_and_absent_account_cannot_start_usage(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                owner_id = uuid4()
                now = datetime.now(UTC)
                result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="unprovisioned",
                        at=now,
                        expires_at=now + timedelta(minutes=1),
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
                assert result.reservation is None
                assert result.access.denial is AccessDenial.ACCOUNT_DELETED
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_deleted_account_with_active_paid_coverage_denies_every_operation(
    billing_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        now = datetime.now(UTC)
        try:
            starts_at = now - timedelta(days=1)
            ends_at = starts_at + timedelta(days=30)
            async with sessions() as session, session.begin():
                session.add(
                    AccessAccountRecord(owner_id=owner_id, deleted_at=now - timedelta(minutes=1))
                )
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=period_id,
                        policy_kind=PeriodKind.PAID.value,
                        policy_version="paid-v1",
                        starts_at=starts_at,
                        ends_at=ends_at,
                        ai_turn_limit=1000,
                        voice_second_limit=3600,
                    )
                )

            async with sessions() as session:
                store = SqlAlchemyAccessUsageStore(session)
                decision = await store.check_access(
                    CheckAccess(owner_id=owner_id, operation=Operation.VIEW_PLAN, at=now)
                )
                assert not decision.allowed
                assert decision.denial is AccessDenial.ACCOUNT_DELETED
            async with sessions() as session, session.begin():
                result = await SqlAlchemyAccessUsageStore(session).reserve_usage(
                    _reservation_command(
                        owner_id=owner_id,
                        request_id="deleted-owner",
                        at=now,
                        expires_at=ends_at,
                        ai_turns=1,
                        voice_seconds=0,
                    )
                )
                assert result.reservation is None
                assert result.access.denial is AccessDenial.ACCOUNT_DELETED
        finally:
            await engine.dispose()

    run_local_async(scenario)


@pytest.mark.parametrize(
    ("mutation", "locking_port"),
    (("delete", "access"), ("expire", "access"), ("delete", "lifecycle")),
)
def test_locked_access_fences_entitlement_changes_and_uses_current_instant(
    billing_database: str, mutation: str, locking_port: str
) -> None:
    async def scenario() -> None:
        engine = create_engine(billing_database)
        sessions = create_session_factory(engine)
        owner_id, period_id = uuid4(), str(uuid4())
        at = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                _, ends_at = _seed_period(
                    session, owner_id=owner_id, period_id=period_id, now=at
                )
            mutation_entered = asyncio.Event()

            async def competing_mutation() -> None:
                async with sessions() as session, session.begin():
                    if mutation == "delete":
                        account = await session.get(AccessAccountRecord, owner_id)
                        assert account is not None
                        account.deleted_at = at
                    else:
                        period = await session.scalar(
                            select(AccessPeriodRecord).where(
                                AccessPeriodRecord.owner_id == owner_id,
                                AccessPeriodRecord.period_id == period_id,
                            )
                        )
                        assert period is not None
                        period.ends_at = at
                    mutation_entered.set()
                    await session.flush()

            async with sessions() as session, session.begin():
                store = SqlAlchemyAccessUsageStore(session)
                if locking_port == "lifecycle":
                    assert await store.lock_account_lifecycle(owner_id) is AccountLifecycle.PRESENT
                    assert await store.lock_account_lifecycle(uuid4()) is AccountLifecycle.MISSING
                check = store.lock_access if locking_port == "access" else store.check_access
                current = await check(
                    CheckAccess(owner_id, Operation.REMINDER_DELIVERY, at)
                )
                assert current.allowed and current.period_id == period_id
                missing = await check(
                    CheckAccess(uuid4(), Operation.REMINDER_DELIVERY, at)
                )
                assert not missing.allowed
                # SQL locks preserve facts, but entitlement time still advances.
                expired = await check(
                    CheckAccess(owner_id, Operation.REMINDER_DELIVERY, ends_at)
                )
                assert not expired.allowed and expired.denial is AccessDenial.ACCESS_EXPIRED
                writer = asyncio.create_task(competing_mutation())
                await asyncio.wait_for(mutation_entered.wait(), timeout=5)
                done, _ = await asyncio.wait({writer}, timeout=0.05)
                assert not done, "access facts must remain locked until caller commits"
            await asyncio.wait_for(writer, timeout=5)
            async with sessions() as session, session.begin():
                store = SqlAlchemyAccessUsageStore(session)
                if locking_port == "lifecycle":
                    assert await store.lock_account_lifecycle(owner_id) is AccountLifecycle.DELETED
                decision = await store.lock_access(
                    CheckAccess(owner_id, Operation.REMINDER_DELIVERY, at)
                )
                assert not decision.allowed
                assert decision.denial is (
                    AccessDenial.ACCOUNT_DELETED
                    if mutation == "delete"
                    else AccessDenial.ACCESS_EXPIRED
                )
                assert await session.scalar(
                    select(func.count()).select_from(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id == owner_id
                    )
                ) == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)
