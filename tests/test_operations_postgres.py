from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import update

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
