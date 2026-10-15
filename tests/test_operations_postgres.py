from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, update

from pin.contexts.retention.api import read_operational_progress
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)
from pin.entrypoints.operations import _snapshot
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.models import WorkerHeartbeat
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
