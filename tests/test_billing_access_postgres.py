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
from sqlalchemy import func, select

from pin.contexts.billing.application.contracts import CheckAccess, ReserveUsage
from pin.contexts.billing.domain.access import (
    AccessDenial,
    InvalidTransition,
    Operation,
    PeriodKind,
    QuotaExceeded,
    ReservationState,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore
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
                    at=expiry, limit=10
                )
                assert count == 1
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
