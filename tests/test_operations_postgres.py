from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.notifications.infrastructure.models import DeliveryAttemptRecord
from pin.contexts.retention.api import read_operational_progress
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.entrypoints.operations import _snapshot
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.jobs import JobLane, claim_job, enqueue_job, fail_job
from pin.platform.models import Job, WorkerHeartbeat
from pin.platform.operations import read_operational_snapshot
from pin.platform.worker_heartbeat import write_worker_heartbeat

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="module")
def operations_database() -> Iterator[str]:
    database_url = os.getenv("PIN_TEST_OPERATIONS_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_OPERATIONS_DATABASE_URL to the guarded issue 22 fixture")

    async def verify_cluster() -> None:
        engine = create_engine(database_url)
        try:
            async with engine.connect() as connection:
                identity = (
                    await connection.exec_driver_sql(
                        "SELECT current_user, current_database(), "
                        "current_setting('server_version'), "
                        "current_setting('data_directory')"
                    )
                ).one()
            user, database, version, data_directory = identity
            if (
                user != "pin_test"
                or not database.startswith("pin_issue22_ops_")
                or not version.startswith("18.6")
                or data_directory.replace("\\", "/") != EXPECTED_DATA_DIRECTORY
            ):
                pytest.fail("operations fixture is outside the authorized disposable cluster")
        finally:
            await engine.dispose()

    run_local_async(verify_cluster)
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    command.check(config)
    yield database_url


def test_worker_heartbeat_and_snapshot_migration_roundtrip(
    operations_database: str,
) -> None:
    config = _alembic_config(operations_database)
    worker_id = f"operations-test-{uuid4()}"

    async def scenario() -> None:
        engine = create_engine(operations_database)
        sessions = create_session_factory(engine)
        try:
            await write_worker_heartbeat(sessions, worker_id=worker_id, role="speech")
            await write_worker_heartbeat(sessions, worker_id=worker_id, role="speech")
            async with sessions() as session, session.begin():
                await session.execute(
                    update(WorkerHeartbeat)
                    .where(WorkerHeartbeat.worker_id == worker_id)
                    .values(last_seen_at=WorkerHeartbeat.last_seen_at - timedelta(seconds=60))
                )
            async with sessions() as session:
                snapshot = await read_operational_snapshot(session)
            worker = next(item for item in snapshot.workers if item["worker_id"] == worker_id)
            assert worker["role"] == "speech" and worker["status"] == "stale"
            assert snapshot.lanes == {"application": {}, "speech": {}, "delivery": {}}
            async with sessions() as session, session.begin():
                heartbeat = await session.get(WorkerHeartbeat, worker_id)
                assert heartbeat is not None
                await session.delete(heartbeat)
        finally:
            await engine.dispose()

    run_local_async(scenario)
    command.downgrade(config, "billing_payments_0001")
    command.upgrade(config, "head")
    command.check(config)


def test_retention_operational_progress_is_bounded_and_redacted(
    operations_database: str,
) -> None:
    owner_id = uuid4()
    request_ids = tuple(uuid4() for _ in range(3))
    pending_request_ids = tuple(uuid4() for _ in range(27))
    unexported_ids = tuple(uuid4() for _ in range(27))
    exported_id = uuid4()
    now = datetime.now(UTC)

    async def scenario() -> None:
        engine = create_engine(operations_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                session.add_all(
                    DeletionRequestRecord(
                        id=request_id,
                        owner_id=owner_id,
                        version=1,
                        state=state,
                        created_at=now - timedelta(hours=2),
                        expires_at=now + timedelta(hours=1),
                        confirmed_at=None,
                        completed_at=(now - timedelta(minutes=5) if state == "completed" else None),
                    )
                    for request_id, state in zip(
                        request_ids,
                        ("previewed", "cancelled", "completed"),
                        strict=True,
                    )
                )
                session.add_all(
                    DeletionRequestRecord(
                        id=request_id,
                        owner_id=owner_id,
                        version=1,
                        state="pending_export",
                        created_at=now - timedelta(hours=2),
                        expires_at=now + timedelta(hours=1),
                        confirmed_at=now - timedelta(minutes=40),
                    )
                    for request_id in pending_request_ids
                )
                session.add_all(
                    DeletionTombstoneRecord(
                        event_id=event_id,
                        owner_id=owner_id,
                        deleted_at=now - timedelta(minutes=30),
                        artifact_version=1,
                    )
                    for event_id in unexported_ids
                )
                session.add(
                    DeletionTombstoneRecord(
                        event_id=exported_id,
                        owner_id=owner_id,
                        deleted_at=now - timedelta(minutes=20),
                        artifact_version=1,
                        exported_at=now,
                    )
                )
            async with sessions() as session:
                projection = await read_operational_progress(session)
            value = projection.as_dict()
            assert value["request_state_counts"] == {
                "previewed": 1,
                "cancelled": 1,
                "pending_export": 27,
                "completed": 1,
            }
            assert 0 < value["oldest_pending_seconds"] < 3600
            assert len(value["pending_request_ids"]) == 25
            assert set(value["pending_request_ids"]).issubset(
                {str(request_id) for request_id in pending_request_ids}
            )
            assert value["unexported_tombstone_count"] == 27
            assert len(value["unexported_tombstone_event_ids"]) == 25
            assert set(value["unexported_tombstone_event_ids"]).issubset(
                {str(event_id) for event_id in unexported_ids}
            )
            assert value["restore_status"] == "unavailable"
            assert str(owner_id) not in str(value)
            assert str(exported_id) not in str(value)
            cli_value, ready = await _snapshot(operations_database)
            assert ready is True
            assert cli_value["restore_and_deletion_progress"]["restore_status"] == "unavailable"
            assert cli_value["restore_and_deletion_progress"]["unexported_tombstone_count"] == 27
            assert str(owner_id) not in str(cli_value)
        finally:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(DeletionRequestRecord).where(
                        DeletionRequestRecord.id.in_((*request_ids, *pending_request_ids))
                    )
                )
                await session.execute(
                    delete(DeletionTombstoneRecord).where(
                        DeletionTombstoneRecord.event_id.in_((*unexported_ids, exported_id))
                    )
                )
            await engine.dispose()

    run_local_async(scenario)


def test_operational_fault_snapshot_reports_bounded_redacted_queue_and_worker_states(
    operations_database: str,
) -> None:
    private_owner_id = uuid4()
    private_task_text = f"private-task-sentinel-{uuid4()}"
    synthetic_secret = f"test-only-api-key-sentinel-{uuid4()}"
    private_error_text = f"private-error-sentinel-{uuid4()}"
    worker_ids = {
        "running": f"operations-running-{uuid4()}",
        "stale": f"operations-stale-{uuid4()}",
        "stopped": f"operations-stopped-{uuid4()}",
    }
    state_counts = {
        JobLane.APPLICATION: 9,
        JobLane.SPEECH: 9,
        JobLane.DELIVERY: 8,
    }
    job_ids: list[UUID] = []
    dead_job_ids: list[UUID] = []
    expired_job_ids: list[UUID] = []
    now = datetime.now(UTC)

    async def scenario() -> None:
        engine = create_engine(operations_database)
        sessions = create_session_factory(engine)

        async def add_job(
            session: AsyncSession,
            *,
            lane: JobLane,
            key: str,
            max_attempts: int = 8,
        ) -> Job:
            job = await enqueue_job(
                session,
                lane=lane,
                kind="operations.synthetic_probe",
                payload={
                    "private_task_text": private_task_text,
                    "api_token": synthetic_secret,
                },
                deduplication_key=key,
                owner_id=private_owner_id,
                max_attempts=max_attempts,
            )
            job_ids.append(job.id)
            return job

        try:
            for lane, count in state_counts.items():
                async with sessions() as session, session.begin():
                    lane_expired_job_ids: list[UUID] = []
                    for index in range(count):
                        job = await add_job(
                            session,
                            lane=lane,
                            key=f"operations-dead-{lane.value}-{index}-{uuid4()}",
                            max_attempts=1,
                        )
                        claim = await claim_job(
                            session,
                            lane=lane,
                            worker_id=f"operations-failing-{lane.value}",
                            lease_for=timedelta(minutes=5),
                        )
                        assert claim is not None and claim.job_id == job.id
                        assert (
                            await fail_job(session, claim, error=private_error_text)
                        ) == "dead"
                        dead_job_ids.append(job.id)

            for lane, count in state_counts.items():
                async with sessions() as session, session.begin():
                    for index in range(count):
                        job = await add_job(
                            session,
                            lane=lane,
                            key=f"operations-expired-{lane.value}-{index}-{uuid4()}",
                        )
                        claim = await claim_job(
                            session,
                            lane=lane,
                            worker_id=f"operations-expiring-{lane.value}",
                            lease_for=timedelta(minutes=5),
                        )
                        assert claim is not None and claim.job_id == job.id
                        lane_expired_job_ids.append(job.id)
                        expired_job_ids.append(job.id)
                    await session.execute(
                        update(Job)
                        .where(Job.id.in_(lane_expired_job_ids))
                        .values(lease_until=now - timedelta(minutes=1))
                    )

            async with sessions() as session, session.begin():
                for lane in JobLane:
                    due_job = await add_job(
                        session,
                        lane=lane,
                        key=f"operations-due-{lane.value}-{uuid4()}",
                    )
                    await session.execute(
                        update(Job)
                        .where(Job.id == due_job.id)
                        .values(created_at=now - timedelta(minutes=10))
                    )
                future_job = await add_job(
                    session,
                    lane=JobLane.APPLICATION,
                    key=f"operations-future-only-{uuid4()}",
                )
                await session.execute(
                    update(Job)
                    .where(Job.id == future_job.id)
                    .values(
                        created_at=now - timedelta(hours=2),
                        available_at=now + timedelta(hours=1),
                    )
                )

            await write_worker_heartbeat(
                sessions, worker_id=worker_ids["stale"], role="application"
            )
            async with sessions() as session, session.begin():
                await session.execute(
                    update(WorkerHeartbeat)
                    .where(WorkerHeartbeat.worker_id == worker_ids["stale"])
                    .values(last_seen_at=now - timedelta(seconds=60))
                )
            await write_worker_heartbeat(
                sessions, worker_id=worker_ids["stopped"], role="speech", stopped=True
            )
            await write_worker_heartbeat(
                sessions, worker_id=worker_ids["running"], role="delivery"
            )

            async with sessions() as session:
                before_jobs = tuple(
                    await session.execute(
                        select(
                            Job.id,
                            Job.status,
                            Job.attempts,
                            Job.available_at,
                            Job.lease_until,
                            Job.finished_at,
                            Job.last_error,
                            Job.payload,
                        )
                        .where(Job.id.in_(job_ids))
                        .order_by(Job.id)
                    )
                )
                before_workers = tuple(
                    await session.execute(
                        select(
                            WorkerHeartbeat.worker_id,
                            WorkerHeartbeat.started_at,
                            WorkerHeartbeat.last_seen_at,
                            WorkerHeartbeat.stopped_at,
                        )
                        .where(WorkerHeartbeat.worker_id.in_(worker_ids.values()))
                        .order_by(WorkerHeartbeat.worker_id)
                    )
                )

            async with sessions() as session, session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))
                snapshot = await read_operational_snapshot(session)

            expected_lanes = {
                lane.value: {
                    "dead": state_counts[lane],
                    "leased": state_counts[lane],
                    "pending": 2 if lane is JobLane.APPLICATION else 1,
                }
                for lane in JobLane
            }
            assert snapshot.lanes == expected_lanes
            assert snapshot.oldest_due_pending_seconds is not None
            assert 500 < snapshot.oldest_due_pending_seconds < 900
            assert len(snapshot.dead_job_ids) == 25
            assert set(snapshot.dead_job_ids).issubset({str(job_id) for job_id in dead_job_ids})
            assert len(snapshot.expired_lease_job_ids) == 25
            assert set(snapshot.expired_lease_job_ids).issubset(
                {str(job_id) for job_id in expired_job_ids}
            )
            workers = {worker["worker_id"]: worker for worker in snapshot.workers}
            assert workers[worker_ids["running"]]["status"] == "running"
            assert workers[worker_ids["stale"]]["status"] == "stale"
            assert workers[worker_ids["stopped"]]["status"] == "stopped"
            assert snapshot.heartbeat_stale_after_seconds == 45

            snapshot_text = repr(snapshot.as_dict())
            cli_snapshot, ready = await _snapshot(operations_database)
            assert ready is True
            cli_text = repr(cli_snapshot)
            for private_value in (
                str(private_owner_id),
                private_task_text,
                synthetic_secret,
                private_error_text,
            ):
                assert private_value not in snapshot_text
                assert private_value not in cli_text

            assert cli_snapshot["lanes"] == expected_lanes
            assert len(cli_snapshot["dead_job_ids"]) == 25
            assert len(cli_snapshot["expired_lease_job_ids"]) == 25

            async with sessions() as session:
                after_jobs = tuple(
                    await session.execute(
                        select(
                            Job.id,
                            Job.status,
                            Job.attempts,
                            Job.available_at,
                            Job.lease_until,
                            Job.finished_at,
                            Job.last_error,
                            Job.payload,
                        )
                        .where(Job.id.in_(job_ids))
                        .order_by(Job.id)
                    )
                )
                after_workers = tuple(
                    await session.execute(
                        select(
                            WorkerHeartbeat.worker_id,
                            WorkerHeartbeat.started_at,
                            WorkerHeartbeat.last_seen_at,
                            WorkerHeartbeat.stopped_at,
                        )
                        .where(WorkerHeartbeat.worker_id.in_(worker_ids.values()))
                        .order_by(WorkerHeartbeat.worker_id)
                    )
                )
            assert after_jobs == before_jobs
            assert after_workers == before_workers
        finally:
            async with sessions() as session, session.begin():
                await session.execute(delete(Job).where(Job.id.in_(job_ids)))
                await session.execute(
                    delete(WorkerHeartbeat).where(
                        WorkerHeartbeat.worker_id.in_(worker_ids.values())
                    )
                )
            await engine.dispose()

    run_local_async(scenario)


def test_context_fault_snapshot_is_redacted_bounded_and_read_only(
    operations_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project synthetic persisted fault facts through the real CLI snapshot."""
    delivery_owner_id = uuid4()
    provider_owner_id = uuid4()
    speech_owner_id = uuid4()
    billing_owner_id = uuid4()
    delivery_ids = (uuid4(), uuid4())
    provider_ids = (uuid4(), uuid4())
    speech_ids = (uuid4(), uuid4())
    reservation_id = uuid4()
    unknown_order_id = uuid4()
    refunded_order_id = uuid4()
    now = datetime.now(UTC)
    private_values = (
        f"private-context-fault-{uuid4()}",
        f"synthetic-provider-secret-{uuid4()}",
        f"synthetic-invoice-payload-{uuid4()}",
        str(delivery_owner_id),
        str(provider_owner_id),
        str(speech_owner_id),
        str(billing_owner_id),
        "987654321012345",
    )

    async def scenario() -> None:
        engine = create_engine(operations_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                session.add_all(
                    [
                        DeliveryAttemptRecord(
                            id=delivery_ids[0],
                            owner_id=delivery_owner_id,
                            intent_id=None,
                            reply_event_id=None,
                            recovery_episode_key=f"synthetic-unknown-{uuid4()}",
                            target_id=987654321012345,
                            dispatch_version=1,
                            attempt_number=1,
                            check_in_cycle_id=None,
                            access_period_id=None,
                            outcome="unknown",
                            failure_code=private_values[0],
                            provider_message_id=None,
                            quiet_conflict=False,
                            started_at=now - timedelta(minutes=2),
                            finished_at=now - timedelta(minutes=1),
                        ),
                        DeliveryAttemptRecord(
                            id=delivery_ids[1],
                            owner_id=delivery_owner_id,
                            intent_id=None,
                            reply_event_id=None,
                            recovery_episode_key=f"synthetic-started-{uuid4()}",
                            target_id=987654321012345,
                            dispatch_version=1,
                            attempt_number=1,
                            check_in_cycle_id=None,
                            access_period_id=None,
                            outcome="started",
                            failure_code=None,
                            provider_message_id=None,
                            quiet_conflict=False,
                            started_at=now - timedelta(seconds=30),
                            finished_at=None,
                        ),
                        ProviderCallRecord(
                            call_id=provider_ids[0],
                            owner_id=provider_owner_id,
                            turn_id=f"synthetic-turn-{uuid4()}",
                            call_number=1,
                            model="synthetic-test-model",
                            state="unknown",
                            provider_request_id=private_values[1],
                            duplicate_of_call_id=None,
                            prompt_tokens=None,
                            completion_tokens=None,
                            total_tokens=None,
                            cost_usd=None,
                            latency_ms=None,
                            result_category="synthetic_test_unknown",
                            created_at=now - timedelta(minutes=2),
                            finished_at=now - timedelta(minutes=1),
                        ),
                        ProviderCallRecord(
                            call_id=provider_ids[1],
                            owner_id=provider_owner_id,
                            turn_id=f"synthetic-turn-{uuid4()}",
                            call_number=1,
                            model="synthetic-test-model",
                            state="started",
                            provider_request_id=None,
                            duplicate_of_call_id=None,
                            prompt_tokens=None,
                            completion_tokens=None,
                            total_tokens=None,
                            cost_usd=None,
                            latency_ms=None,
                            result_category="pending",
                            created_at=now - timedelta(seconds=30),
                            finished_at=None,
                        ),
                        SpeechRequestRecord(
                            job_id=speech_ids[0],
                            owner_id=speech_owner_id,
                            inbox_id=uuid4(),
                            request_key=f"synthetic-failed-{uuid4()}",
                            state="failed",
                            declared_size_bytes=1024,
                            declared_duration_seconds=1.5,
                            actual_size_bytes=None,
                            decoded_duration_seconds=None,
                            transcript=None,
                            language=None,
                            failure_code=private_values[0],
                            created_at=now - timedelta(minutes=2),
                            updated_at=now - timedelta(minutes=1),
                        ),
                        SpeechRequestRecord(
                            job_id=speech_ids[1],
                            owner_id=speech_owner_id,
                            inbox_id=uuid4(),
                            request_key=f"synthetic-pending-{uuid4()}",
                            state="processing",
                            declared_size_bytes=1024,
                            declared_duration_seconds=1.5,
                            actual_size_bytes=None,
                            decoded_duration_seconds=None,
                            transcript=None,
                            language=None,
                            failure_code=None,
                            created_at=now - timedelta(minutes=1),
                            updated_at=now - timedelta(seconds=30),
                        ),
                        AccessAccountRecord(owner_id=billing_owner_id, created_at=now),
                        AccessPeriodRecord(
                            owner_id=billing_owner_id,
                            period_id="synthetic-expired-period",
                            policy_kind="paid",
                            policy_version="synthetic-test-v1",
                            starts_at=now - timedelta(days=31),
                            ends_at=now - timedelta(days=1),
                            ai_turn_limit=10,
                            voice_second_limit=600,
                        ),
                        UsageReservationRecord(
                            id=reservation_id,
                            owner_id=billing_owner_id,
                            period_id="synthetic-expired-period",
                            request_id=f"synthetic-reservation-{uuid4()}",
                            reserved_ai_turns=1,
                            reserved_voice_seconds=0,
                            settled_ai_turns=0,
                            settled_voice_seconds=0,
                            state="reserved",
                            created_at=now - timedelta(minutes=5),
                            expires_at=now - timedelta(minutes=1),
                            finalized_at=None,
                        ),
                        PaymentOrderRecord(
                            id=unknown_order_id,
                            owner_id=billing_owner_id,
                            telegram_user_id=987654321012345,
                            invoice_payload=private_values[2],
                            terms_version="synthetic-test-terms",
                            state="open",
                            pre_checkout_query_id=None,
                            invoice_status="unknown",
                            invoice_started_at=now - timedelta(minutes=2),
                            invoice_message_id=None,
                            invoice_error_code=private_values[0],
                            first_charge_id=None,
                            latest_period_end=None,
                            renewal_cancelled_at=None,
                            cancel_status=None,
                            cancel_requested_at=None,
                            cancel_started_at=None,
                            cancel_error_code=None,
                            created_at=now - timedelta(minutes=3),
                        ),
                        PaymentOrderRecord(
                            id=refunded_order_id,
                            owner_id=billing_owner_id,
                            telegram_user_id=987654321012345,
                            invoice_payload=f"synthetic-paid-{uuid4()}",
                            terms_version="synthetic-test-terms",
                            state="paid",
                            pre_checkout_query_id=None,
                            invoice_status="sent",
                            invoice_started_at=now - timedelta(days=31),
                            invoice_message_id=1,
                            invoice_error_code=None,
                            first_charge_id=f"synthetic-charge-{uuid4()}",
                            latest_period_end=now - timedelta(days=1),
                            renewal_cancelled_at=None,
                            cancel_status=None,
                            cancel_requested_at=None,
                            cancel_started_at=None,
                            cancel_error_code=None,
                            created_at=now - timedelta(days=32),
                        ),
                        PaymentReceiptRecord(
                            telegram_charge_id=f"synthetic-charge-{uuid4()}",
                            provider_charge_id=f"synthetic-provider-charge-{uuid4()}",
                            order_id=refunded_order_id,
                            owner_id=billing_owner_id,
                            telegram_user_id=987654321012345,
                            currency="XTR",
                            total_amount=250,
                            is_recurring=True,
                            is_first_recurring=True,
                            period_starts_at=now - timedelta(days=32),
                            period_ends_at=now - timedelta(days=1),
                            received_at=now - timedelta(days=32),
                            refunded_at=None,
                        ),
                    ]
                )
                await session.flush()
                receipt = await session.scalar(
                    select(PaymentReceiptRecord).where(
                        PaymentReceiptRecord.order_id == refunded_order_id
                    )
                )
                assert receipt is not None
                session.add(
                    PaymentRefundRecord(
                        telegram_charge_id=receipt.telegram_charge_id,
                        requested_by="synthetic-operator",
                        status="unknown",
                        requested_at=now - timedelta(minutes=2),
                        finished_at=None,
                    )
                )

            sql_statements: list[str] = []
            original_create_engine = create_engine

            def create_observed_engine(database_url: str) -> AsyncEngine:
                observed_engine = original_create_engine(database_url)

                def guard_snapshot_sql(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    normalized = statement.lstrip().upper()
                    sql_statements.append(normalized)
                    if not normalized.startswith(("SELECT", "SHOW")):
                        raise AssertionError("the operational snapshot issued non-read SQL")

                event.listen(
                    observed_engine.sync_engine,
                    "before_cursor_execute",
                    guard_snapshot_sql,
                )
                return observed_engine

            monkeypatch.setattr(
                "pin.entrypoints.operations.create_engine", create_observed_engine
            )

            async def read_fault_facts() -> dict[str, list[tuple[str, ...]]]:
                async with sessions() as session:
                    delivery_rows = await session.execute(
                        select(DeliveryAttemptRecord.id, DeliveryAttemptRecord.outcome)
                        .where(DeliveryAttemptRecord.id.in_(delivery_ids))
                        .order_by(DeliveryAttemptRecord.id)
                    )
                    provider_rows = await session.execute(
                        select(ProviderCallRecord.call_id, ProviderCallRecord.state)
                        .where(ProviderCallRecord.call_id.in_(provider_ids))
                        .order_by(ProviderCallRecord.call_id)
                    )
                    speech_rows = await session.execute(
                        select(SpeechRequestRecord.job_id, SpeechRequestRecord.state)
                        .where(SpeechRequestRecord.job_id.in_(speech_ids))
                        .order_by(SpeechRequestRecord.job_id)
                    )
                    reservation_rows = await session.execute(
                        select(UsageReservationRecord.id, UsageReservationRecord.state)
                        .where(UsageReservationRecord.id == reservation_id)
                    )
                    order_rows = await session.execute(
                        select(PaymentOrderRecord.id, PaymentOrderRecord.invoice_status)
                        .where(PaymentOrderRecord.id.in_((unknown_order_id, refunded_order_id)))
                        .order_by(PaymentOrderRecord.id)
                    )
                    receipt_rows = await session.execute(
                        select(PaymentReceiptRecord.telegram_charge_id)
                        .where(PaymentReceiptRecord.order_id == refunded_order_id)
                    )
                    refund_rows = await session.execute(
                        select(PaymentRefundRecord.telegram_charge_id, PaymentRefundRecord.status)
                        .where(
                            PaymentRefundRecord.telegram_charge_id.in_(
                                select(PaymentReceiptRecord.telegram_charge_id).where(
                                    PaymentReceiptRecord.order_id == refunded_order_id
                                )
                            )
                        )
                    )
                    return {
                        "delivery": [(str(row.id), row.outcome) for row in delivery_rows],
                        "provider": [(str(row.call_id), row.state) for row in provider_rows],
                        "speech": [(str(row.job_id), row.state) for row in speech_rows],
                        "reservation": [(str(row.id), row.state) for row in reservation_rows],
                        "orders": [(str(row.id), row.invoice_status) for row in order_rows],
                        "receipts": [(str(row.telegram_charge_id),) for row in receipt_rows],
                        "refunds": [
                            (str(row.telegram_charge_id), row.status) for row in refund_rows
                        ],
                    }

            facts_before_snapshot = await read_fault_facts()
            value, ready = await _snapshot(operations_database)
            assert ready is True
            assert sql_statements
            assert all(statement.startswith(("SELECT", "SHOW")) for statement in sql_statements)
            assert await read_fault_facts() == facts_before_snapshot

            contexts = value["contexts"]
            delivery = contexts["delivery"]
            assert delivery["outcomes"]["unknown"] == 1
            assert delivery["outcomes"]["started"] == 1
            assert delivery["unknown_attempt_ids"] == (str(delivery_ids[0]),)
            assert delivery["started_attempts"][0]["attempt_id"] == str(delivery_ids[1])
            assert len(delivery["unknown_attempt_ids"]) <= 25
            assert len(delivery["started_attempts"]) <= 25

            provider = contexts["provider"]
            assert provider["states"] == {"started": 1, "unknown": 1}
            assert provider["unknown_call_ids"] == (str(provider_ids[0]),)
            assert provider["started_call_ids"] == (str(provider_ids[1]),)
            assert len(provider["unknown_call_ids"]) <= 25
            assert len(provider["started_call_ids"]) <= 25
            assert provider["reported_tokens"] == 0
            assert provider["reported_cost_usd"] == "0"

            speech = contexts["speech"]
            assert speech["states"] == {"failed": 1, "processing": 1}
            assert speech["failed_job_ids"] == (str(speech_ids[0]),)
            assert len(speech["failed_job_ids"]) <= 25

            billing = contexts["billing"]
            assert billing["reservation_states"] == {"reserved": 1}
            assert billing["overdue_reservation_ids"] == (str(reservation_id),)
            assert len(billing["overdue_reservation_ids"]) <= 25
            assert billing["invoice_states"] == {"sent": 1, "unknown": 1}
            assert billing["uncertain_order_ids"] == (str(unknown_order_id),)
            assert len(billing["uncertain_order_ids"]) <= 25
            assert billing["refund_states"] == {"unknown": 1}
            assert billing["uncertain_refund_order_ids"] == (str(refunded_order_id),)
            assert len(billing["uncertain_refund_order_ids"]) <= 25
            assert billing["receipt_count"] == 1
            assert billing["receipt_total_stars"] == 250

            snapshot_text = repr(value)
            for private_value in private_values:
                assert private_value not in snapshot_text
            for owner_id in (
                delivery_owner_id,
                provider_owner_id,
                speech_owner_id,
                billing_owner_id,
            ):
                assert str(owner_id) not in snapshot_text
        finally:
            async with sessions() as session, session.begin():
                await session.execute(
                    delete(PaymentRefundRecord).where(
                        PaymentRefundRecord.telegram_charge_id.in_(
                            select(PaymentReceiptRecord.telegram_charge_id).where(
                                PaymentReceiptRecord.order_id == refunded_order_id
                            )
                        )
                    )
                )
                await session.execute(
                    delete(PaymentReceiptRecord).where(
                        PaymentReceiptRecord.order_id == refunded_order_id
                    )
                )
                await session.execute(
                    delete(PaymentOrderRecord).where(
                        PaymentOrderRecord.id.in_((unknown_order_id, refunded_order_id))
                    )
                )
                await session.execute(
                    delete(UsageReservationRecord).where(
                        UsageReservationRecord.id == reservation_id
                    )
                )
                await session.execute(
                    delete(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == billing_owner_id
                    )
                )
                await session.execute(
                    delete(AccessAccountRecord).where(
                        AccessAccountRecord.owner_id == billing_owner_id
                    )
                )
                await session.execute(
                    delete(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.id.in_(delivery_ids)
                    )
                )
                await session.execute(
                    delete(ProviderCallRecord).where(ProviderCallRecord.call_id.in_(provider_ids))
                )
                await session.execute(
                    delete(SpeechRequestRecord).where(SpeechRequestRecord.job_id.in_(speech_ids))
                )
            await engine.dispose()

    run_local_async(scenario)
