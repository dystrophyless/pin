from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    insert,
    select,
    text,
)
from sqlalchemy import (
    create_engine as create_sync_engine,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema

from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.models import ActionReceipt
from pin.persistence.receipts import find_action_receipt, lock_action, record_action_receipt
from pin.persistence.uow import SqlAlchemyUnitOfWork
from pin.persistence.versions import OptimisticConcurrencyConflict, update_versioned

ROOT = os.path.dirname(os.path.dirname(__file__))
TEST_SCHEMA = "pin_issue2_verification"
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
metadata = MetaData()
operation_records = Table(
    "operation_records",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("owner_id", Uuid(as_uuid=True), nullable=False),
    Column("title", String(100), nullable=False),
    Column("version", Integer, nullable=False, server_default=text("1")),
    CheckConstraint("version >= 1", name="ck_issue2_operation_version"),
    schema=TEST_SCHEMA,
)
integration_events = Table(
    "integration_events",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("owner_id", Uuid(as_uuid=True), nullable=False),
    Column("aggregate_id", Uuid(as_uuid=True), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("event_type", String(100), nullable=False),
    UniqueConstraint("aggregate_id", "aggregate_version", name="uq_issue2_event_version"),
    schema=TEST_SCHEMA,
)


def _alembic_config(database_url: str) -> Config:
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def transaction_database() -> Iterator[tuple[str, str]]:
    database_url = os.getenv("PIN_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_DATABASE_URL to a disposable PostgreSQL database")
    command.upgrade(_alembic_config(database_url), "persistence_0001")
    sync_engine = create_sync_engine(database_url)
    try:
        with sync_engine.begin() as connection:
            connection.execute(CreateSchema(TEST_SCHEMA, if_not_exists=True))
            metadata.create_all(connection)
        yield database_url, TEST_SCHEMA
    finally:
        with sync_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        sync_engine.dispose()


@pytest.mark.asyncio
async def test_sessions_are_isolated_and_committed_rows_become_visible(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    identity = uuid4()
    owner = uuid4()
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as writer:
            await writer.session.execute(
                insert(operation_records).values(id=identity, owner_id=owner, title="private")
            )
            async with SqlAlchemyUnitOfWork(session_factory) as reader:
                result = await reader.session.scalar(
                    select(operation_records.c.id).where(operation_records.c.id == identity)
                )
                assert result is None

        async with SqlAlchemyUnitOfWork(session_factory) as reader:
            result = await reader.session.scalar(
                select(operation_records.c.title).where(operation_records.c.id == identity)
            )
            assert result == "private"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_uow_rolls_back_mutation_receipt_and_event_at_every_write_boundary(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)

    class InjectedFailure(Exception):
        pass

    try:
        for failed_after in ("mutation", "receipt", "event"):
            owner, operation_id, event_id = uuid4(), uuid4(), uuid4()
            action_key = f"rollback-{uuid4()}"
            with pytest.raises(InjectedFailure):
                async with SqlAlchemyUnitOfWork(session_factory) as uow:
                    await lock_action(uow.session, owner, action_key)
                    await uow.session.execute(
                        insert(operation_records).values(
                            id=operation_id, owner_id=owner, title="must roll back"
                        )
                    )
                    if failed_after == "mutation":
                        raise InjectedFailure

                    await record_action_receipt(
                        uow.session, owner, action_key, {"operation_id": str(operation_id)}
                    )
                    if failed_after == "receipt":
                        raise InjectedFailure

                    await uow.session.execute(
                        insert(integration_events).values(
                            id=event_id,
                            owner_id=owner,
                            aggregate_id=operation_id,
                            aggregate_version=1,
                            event_type="operation.created",
                        )
                    )
                    raise InjectedFailure

            async with SqlAlchemyUnitOfWork(session_factory) as verify:
                assert (
                    await verify.session.scalar(
                        select(operation_records.c.id).where(operation_records.c.id == operation_id)
                    )
                    is None
                )
                assert (
                    await verify.session.scalar(
                        select(integration_events.c.id).where(integration_events.c.id == event_id)
                    )
                    is None
                )
                assert await find_action_receipt(verify.session, owner, action_key) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_receipt_replay_returns_original_result_and_owner_keys_are_isolated(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    owner, other_owner, operation_id, event_id = uuid4(), uuid4(), uuid4(), uuid4()
    action_key = f"replay-{uuid4()}"
    original_result = {"operation_id": str(operation_id), "version": 1}
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await lock_action(uow.session, owner, action_key)
            await uow.session.execute(
                insert(operation_records).values(
                    id=operation_id, owner_id=owner, title="created once"
                )
            )
            await uow.session.execute(
                insert(integration_events).values(
                    id=event_id,
                    owner_id=owner,
                    aggregate_id=operation_id,
                    aggregate_version=1,
                    event_type="operation.created",
                )
            )
            await record_action_receipt(uow.session, owner, action_key, original_result)

        async with SqlAlchemyUnitOfWork(session_factory) as replay:
            await lock_action(replay.session, owner, action_key)
            receipt = await find_action_receipt(replay.session, owner, action_key)
            assert receipt is not None
            assert receipt.result == original_result
            same_owner = await record_action_receipt(
                replay.session, owner, action_key, {"must": "not replace original"}
            )
            other_owner_receipt = await record_action_receipt(
                replay.session, other_owner, action_key, {"owner": "separate"}
            )
            assert same_owner.result == original_result
            assert other_owner_receipt.result == {"owner": "separate"}

        async with SqlAlchemyUnitOfWork(session_factory) as verify:
            assert (
                await verify.session.scalar(
                    select(operation_records.c.id).where(operation_records.c.id == operation_id)
                )
                == operation_id
            )
            assert (
                await verify.session.scalar(
                    select(integration_events.c.id).where(integration_events.c.id == event_id)
                )
                == event_id
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_command_commits_one_mutation_event_and_receipt(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    owner, action_key = uuid4(), f"concurrent-{uuid4()}"
    both_started = asyncio.Barrier(2)

    async def apply_command(title: str) -> dict[str, str]:
        await both_started.wait()
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await lock_action(uow.session, owner, action_key)
            receipt = await find_action_receipt(uow.session, owner, action_key)
            if receipt is not None:
                return receipt.result

            operation_id = uuid4()
            await uow.session.execute(
                insert(operation_records).values(
                    id=operation_id, owner_id=owner, title=title, version=1
                )
            )
            await uow.session.execute(
                insert(integration_events).values(
                    id=uuid4(),
                    owner_id=owner,
                    aggregate_id=operation_id,
                    aggregate_version=1,
                    event_type="operation.created",
                )
            )
            result = {"operation_id": str(operation_id), "version": 1}
            await record_action_receipt(uow.session, owner, action_key, result)
            return result

    try:
        first, second = await asyncio.gather(apply_command("first"), apply_command("second"))
        assert first == second

        async with SqlAlchemyUnitOfWork(session_factory) as verify:
            persisted_id = await verify.session.scalar(
                select(operation_records.c.id).where(operation_records.c.owner_id == owner)
            )
            assert str(persisted_id) == first["operation_id"]
            assert (
                await verify.session.scalar(
                    select(operation_records.c.id)
                    .where(operation_records.c.owner_id == owner)
                    .offset(1)
                )
                is None
            )
            assert (
                await verify.session.scalar(
                    select(integration_events.c.id).where(integration_events.c.owner_id == owner)
                )
                is not None
            )
            assert (
                await verify.session.scalar(
                    select(integration_events.c.id)
                    .where(integration_events.c.owner_id == owner)
                    .offset(1)
                )
                is None
            )
            receipt = await find_action_receipt(verify.session, owner, action_key)
            assert receipt is not None
            assert receipt.result == first
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_competing_stale_writes_have_one_winner_and_one_conflict(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    identity, owner = uuid4(), uuid4()
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as setup:
            await setup.session.execute(
                insert(operation_records).values(
                    id=identity, owner_id=owner, title="initial", version=1
                )
            )

        both_read = asyncio.Barrier(2)

        async def compete(title: str) -> int:
            async with SqlAlchemyUnitOfWork(session_factory) as uow:
                version = await uow.session.scalar(
                    select(operation_records.c.version).where(operation_records.c.id == identity)
                )
                assert version == 1
                await both_read.wait()
                return await update_versioned(
                    uow.session,
                    operation_records,
                    identity,
                    expected_version=1,
                    values={"title": title},
                )

        outcomes = await asyncio.gather(
            compete("winner A"), compete("winner B"), return_exceptions=True
        )
        successes = [outcome for outcome in outcomes if outcome == 2]
        conflicts = [
            outcome for outcome in outcomes if isinstance(outcome, OptimisticConcurrencyConflict)
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1

        async with SqlAlchemyUnitOfWork(session_factory) as verify:
            row = (
                await verify.session.execute(
                    select(operation_records.c.title, operation_records.c.version).where(
                        operation_records.c.id == identity
                    )
                )
            ).one()
            assert row.version == 2
            assert row.title in {"winner A", "winner B"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_action_receipt_unique_key_is_enforced_by_postgresql(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    owner, action_key = uuid4(), f"unique-{uuid4()}"
    try:
        async with SqlAlchemyUnitOfWork(session_factory) as first:
            await record_action_receipt(first.session, owner, action_key, {"result": 1})
        with pytest.raises(IntegrityError):
            async with SqlAlchemyUnitOfWork(session_factory) as duplicate:
                await duplicate.session.execute(
                    insert(ActionReceipt).values(
                        owner_id=owner, action_key=action_key, result={"result": 2}
                    )
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_action_receipt_key_validation_is_database_enforced(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    try:
        with pytest.raises(IntegrityError):
            async with SqlAlchemyUnitOfWork(session_factory) as invalid:
                invalid.session.add(ActionReceipt(owner_id=uuid4(), action_key="  ", result={}))
                await invalid.session.flush()
    finally:
        await engine.dispose()


def test_canonical_local_runner_connects_to_postgresql(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database

    async def check_connection() -> int:
        engine = create_engine(database_url)
        try:
            async with engine.connect() as connection:
                return int(await connection.scalar(text("SELECT 1")))
        finally:
            await engine.dispose()

    assert run_local_async(check_connection) == 1


def test_worker_crash_rolls_back_partial_command_and_retry_replays_commit(
    transaction_database: tuple[str, str],
) -> None:
    database_url, _ = transaction_database
    owner, operation_id, event_id = uuid4(), uuid4(), uuid4()
    action_key = f"crash-{uuid4()}"
    original_result = {"operation_id": str(operation_id), "version": 1}

    child_source = """
import os
import sys
from sqlalchemy import insert, text
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.receipts import lock_action, record_action_receipt
from pin.persistence.uow import SqlAlchemyUnitOfWork
from uuid import UUID

database_url, owner_id, operation_id, event_id, action_key, ready_path = sys.argv[1:]
owner, operation, event = UUID(owner_id), UUID(operation_id), UUID(event_id)

async def write_partial_command():
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    async with SqlAlchemyUnitOfWork(factory) as uow:
        await lock_action(uow.session, owner, action_key)
        await uow.session.execute(text(
            'INSERT INTO pin_issue2_verification.operation_records '
            '(id, owner_id, title, version) VALUES (:id, :owner, :title, 1)'
        ), {"id": operation, "owner": owner, "title": "uncommitted"})
        await record_action_receipt(
            uow.session, owner, action_key,
            {"operation_id": str(operation), "version": 1},
        )
        backend_pid = await uow.session.scalar(text("SELECT pg_backend_pid()"))
        temporary_ready_path = ready_path + ".tmp"
        with open(temporary_ready_path, "w", encoding="ascii") as ready:
            ready.write(str(backend_pid))
        os.replace(temporary_ready_path, ready_path)
        sys.stdin.buffer.read(1)
        os._exit(73)

run_local_async(write_partial_command)
"""

    with tempfile.TemporaryDirectory(prefix="pin-issue2-crash-") as temp_dir:
        ready_path = Path(temp_dir) / "backend.pid"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_source,
                database_url,
                str(owner),
                str(operation_id),
                str(event_id),
                action_key,
                str(ready_path),
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not ready_path.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    _, stderr = process.communicate()
                    raise AssertionError(
                        f"crash worker exited before opening its transaction: "
                        f"{stderr.decode(errors='replace')}"
                    )
                time.sleep(0.05)
            assert ready_path.exists(), "crash worker did not reach the partial-write boundary"
            backend_pid = int(ready_path.read_text(encoding="ascii"))

            sync_engine = create_sync_engine(database_url)
            try:
                with sync_engine.connect() as connection:
                    parent_pid = connection.exec_driver_sql(
                        "SELECT pg_backend_pid()"
                    ).scalar_one()
                    assert backend_pid != parent_pid
                    backend = connection.exec_driver_sql(
                        "SELECT datname FROM pg_stat_activity WHERE pid = %s",
                        (backend_pid,),
                    ).scalar_one_or_none()
                    assert backend == make_url(database_url).database
            finally:
                sync_engine.dispose()

            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            assert process.wait(timeout=15) == 73
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            if process.stderr is not None:
                process.stderr.close()

    sync_engine = create_sync_engine(database_url)
    try:
        with sync_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pg_stat_activity WHERE pid = %s", (backend_pid,)
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pin_issue2_verification.operation_records WHERE id = %s",
                (operation_id,),
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pin_persistence.action_receipts "
                "WHERE owner_id = %s AND action_key = %s",
                (owner, action_key),
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pin_issue2_verification.integration_events "
                "WHERE id = %s",
                (event_id,),
            ).scalar_one() == 0
    finally:
        sync_engine.dispose()

    async def retry_and_replay() -> None:
        engine = create_engine(database_url)
        factory = create_session_factory(engine)
        try:
            async with SqlAlchemyUnitOfWork(factory) as retry:
                await lock_action(retry.session, owner, action_key)
                assert await find_action_receipt(retry.session, owner, action_key) is None
                await retry.session.execute(
                    insert(operation_records).values(
                        id=operation_id,
                        owner_id=owner,
                        title="committed retry",
                        version=1,
                    )
                )
                await retry.session.execute(
                    insert(integration_events).values(
                        id=event_id,
                        owner_id=owner,
                        aggregate_id=operation_id,
                        aggregate_version=1,
                        event_type="operation.created",
                    )
                )
                await record_action_receipt(retry.session, owner, action_key, original_result)

            async with SqlAlchemyUnitOfWork(factory) as replay:
                await lock_action(replay.session, owner, action_key)
                receipt = await find_action_receipt(replay.session, owner, action_key)
                assert receipt is not None
                assert receipt.result == original_result
        finally:
            await engine.dispose()

    run_local_async(retry_and_replay)
