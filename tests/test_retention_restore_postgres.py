from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.retention.deletion import finish_pending_deletions
from pin.adapters.telegram.deletion import purge_account
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    RetainedBillingIdentityRecord,
    TrialActivationRecord,
)
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.notifications.application.delivery import (
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.retention.infrastructure.store import (
    confirm_deletion_preview,
    create_deletion_preview,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobLane, enqueue_job
from pin.platform.models import Job

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATABASE_ENV = "PIN_TEST_RETENTION_RESTORE_SOURCE_DATABASE_URL"
TARGET_DATABASE_ENV = "PIN_TEST_RETENTION_RESTORE_TARGET_DATABASE_URL"
DATABASE_HOST = "127.0.0.1"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATABASE_VERSION = "18.6"
DATABASE_VERSION_NUM = 180006
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
PG_BIN = Path("D:/Program/postgresql/bin")
DATABASE_PATTERNS = {
    "source": re.compile(r"pin_issue23_restore_source_[a-z0-9]+\Z"),
    "target": re.compile(r"pin_issue23_restore_target_[a-z0-9]+\Z"),
}


class _UncertainSendTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.sent: list[ReminderSend] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int:
        value = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )
        assert value is not None
        return value

    async def send_reminder(self, message: ReminderSend) -> str:
        self.sent.append(message)
        self.started.set()
        await self.finish.wait()
        raise UnknownDeliveryFailure("synthetic_snapshot_window_unknown")

    async def send_reply(self, message: TelegramReplySend) -> str:
        raise AssertionError("restore fixture must not dispatch a reply")


def _database_url(role: str) -> URL:
    env_name = SOURCE_DATABASE_ENV if role == "source" else TARGET_DATABASE_ENV
    value = os.getenv(env_name)
    if value is None:
        pytest.skip(f"set {env_name} to the assigned fresh restore fixture")
    url = make_url(value)
    pattern = DATABASE_PATTERNS[role]
    if (
        url.drivername != "postgresql+psycopg"
        or (url.host, url.port, url.username) != (DATABASE_HOST, DATABASE_PORT, DATABASE_USER)
        or url.password is not None
        or url.query
        or url.database is None
        or pattern.fullmatch(url.database) is None
    ):
        pytest.fail(f"{role} restore fixture URL does not match its dedicated local identity")
    return url


def _plain_libpq_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=True)


def _verify_server(url: URL, role: str, *, require_empty: bool) -> None:
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("SELECT current_database()").scalar_one())
            port = int(connection.exec_driver_sql("SHOW port").scalar_one())
            user = str(connection.exec_driver_sql("SELECT current_user").scalar_one())
            version = str(connection.exec_driver_sql("SHOW server_version").scalar_one())
            version_num = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
            directory = str(connection.exec_driver_sql("SHOW data_directory").scalar_one())
            has_migrations = bool(
                connection.exec_driver_sql(
                    "SELECT to_regclass('public.alembic_version') IS NOT NULL"
                ).scalar_one()
            )
            has_tables = bool(
                connection.exec_driver_sql(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema'))"
                ).scalar_one()
            )
    finally:
        engine.dispose()
    if (
        name != url.database
        or port != DATABASE_PORT
        or user != DATABASE_USER
        or version != DATABASE_VERSION
        or version_num != DATABASE_VERSION_NUM
        or directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold()
    ):
        pytest.fail(f"{role} fixture is not the assigned PostgreSQL 18.6 local cluster")
    if require_empty and (has_migrations or has_tables):
        pytest.fail("restore target must be a fresh empty manifest-owned database")


def _run(
    command_args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command_args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )


def _require_pg_tools() -> tuple[Path, Path]:
    dump = PG_BIN / "pg_dump.exe"
    restore = PG_BIN / "pg_restore.exe"
    if not dump.is_file() or not restore.is_file():
        pytest.fail("assigned PostgreSQL 18.6 pg_dump/pg_restore binaries are unavailable")
    for executable in (dump, restore):
        result = _run([str(executable), "--version"])
        if result.returncode != 0 or "18.6" not in result.stdout:
            pytest.fail(
                "pg_dump/pg_restore version does not match the assigned PostgreSQL 18.6 cluster"
            )
    return dump, restore


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def _seed_deleted_owner(
    sessions: async_sessionmaker[AsyncSession], owner_id: UUID, *, at: datetime
) -> None:
    telegram_user_id = owner_id.int % 2_000_000_000 + 1
    async with sessions() as session, session.begin():
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            RetainedBillingIdentityRecord(
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                retained_at=at - timedelta(days=30),
            )
        )
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id="retained-paid-period",
                policy_kind="paid",
                policy_version="restore-test-policy-v1",
                starts_at=at - timedelta(days=10),
                ends_at=at + timedelta(days=20),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            TrialActivationRecord(
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                period_id="retained-trial-period",
                activated_at=at - timedelta(days=90),
            )
        )
        session.add(PlanItemRecord(owner_id=owner_id, kind="task", title="must be purged"))


async def _seed_started_send_case(
    sessions: async_sessionmaker[AsyncSession], *, at: datetime
) -> tuple[UUID, UUID]:
    owner_id, plan_id, occurrence_id, intent_id = uuid4(), uuid4(), uuid4(), uuid4()
    telegram_id = owner_id.int % 2_000_000_000 + 1
    starts_at = at + timedelta(hours=3)
    ends_at = starts_at + timedelta(minutes=30)
    due_at = starts_at - timedelta(hours=4)
    event = EventEnvelope(
        id=uuid4(),
        event_type="planning.plan_created",
        schema_version=1,
        aggregate_id=occurrence_id,
        aggregate_version=1,
        owner_id=owner_id,
        occurred_at=at,
        payload={},
    )
    async with sessions() as session, session.begin():
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
                language="en",
                home_zone="UTC",
                reminder_calendar_days=1,
                reminder_elapsed_hours=4,
                quiet_start=(at + timedelta(hours=1)).time(),
                quiet_end=(at + timedelta(hours=2)).time(),
                review_time=time(20),
                version=1,
            )
        )
        session.add(TelegramOwnerRecord(bot_id=1, telegram_user_id=telegram_id, owner_id=owner_id))
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id="survivor-paid-period",
                policy_kind="paid",
                policy_version="restore-test-policy-v1",
                starts_at=at - timedelta(days=1),
                ends_at=at + timedelta(days=7),
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
        session.add(
            PlanItemRecord(
                id=plan_id, owner_id=owner_id, kind="appointment", title="survivor reminder"
            )
        )
        await session.flush()
        session.add(
            OccurrenceRecord(
                id=occurrence_id,
                owner_id=owner_id,
                plan_item_id=plan_id,
                kind="appointment",
                event_zone="UTC",
                local_date=starts_at.date(),
                local_time=starts_at.time().replace(tzinfo=None),
                end_local_date=ends_at.date(),
                end_local_time=ends_at.time().replace(tzinfo=None),
                starts_at=starts_at,
                ends_at=ends_at,
                deadline_at=None,
                duration_seconds=1800,
                state="open",
                version=1,
                reminder_preferences_version=1,
            )
        )
        await session.flush()
        session.add(
            ReminderRuleRecord(
                id=uuid4(),
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind="elapsed_seconds",
                offset_amount=4 * 60 * 60,
                source="explicit",
                due_at=due_at,
            )
        )
        await add_outbox_event(session, event)
        session.add(
            ReminderIntentRecord(
                id=intent_id,
                owner_id=owner_id,
                plan_item_id=plan_id,
                occurrence_id=occurrence_id,
                series_id=None,
                plan_version=1,
                occurrence_version=1,
                preferences_version=1,
                series_version=None,
                offset_kind="elapsed_seconds",
                offset_amount=4 * 60 * 60,
                source="explicit",
                due_at=due_at,
                not_before_at=None,
                state="scheduled",
                dispatch_version=1,
                source_event_id=event.id,
            )
        )
        await enqueue_job(
            session,
            lane=JobLane.DELIVERY,
            kind="notifications.deliver_reminder",
            payload={"intent_id": str(intent_id), "dispatch_version": 1},
            deduplication_key=f"reminder:{intent_id}:1",
            owner_id=owner_id,
            priority=1_000_000,
        )
    return owner_id, intent_id


def test_dump_restore_replays_deletion_before_recovering_started_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_url = _database_url("source")
    target_url = _database_url("target")
    if source_url.database == target_url.database:
        pytest.fail("restore source and target must be different fresh databases")
    _verify_server(source_url, "source", require_empty=True)
    _verify_server(target_url, "target", require_empty=True)
    dump_tool, restore_tool = _require_pg_tools()

    source_string = source_url.render_as_string(hide_password=True)
    monkeypatch.setenv("PIN_DATABASE_URL", source_string)
    command.upgrade(_alembic_config(source_string), "head")
    source_engine = create_engine(source_url.render_as_string(hide_password=True))
    source_sessions = create_session_factory(source_engine)
    target_engine = None
    dump_path = tmp_path / "snapshot.dump"
    ledger_path = tmp_path / "deletions.jsonl"
    deleted_owner = uuid4()
    at = datetime.now(UTC).replace(microsecond=0)

    async def scenario() -> None:
        nonlocal target_engine
        await _seed_deleted_owner(source_sessions, deleted_owner, at=at)
        send_owner, intent_id = await _seed_started_send_case(source_sessions, at=at)
        transport = _UncertainSendTransport()
        worker = asyncio.create_task(
            process_delivery_iteration(
                source_sessions,
                transport,
                worker_id=f"restore-source-{uuid4().hex[:8]}",
                now=lambda: at,
            )
        )
        try:
            await asyncio.wait_for(transport.started.wait(), timeout=20)
            async with source_sessions() as session:
                attempt = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == intent_id
                    )
                )
                assert attempt is not None and attempt.outcome == "started"

            dump = await asyncio.to_thread(
                _run,
                [
                    str(dump_tool),
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    f"--file={dump_path}",
                    f"--dbname={_plain_libpq_url(source_url)}",
                ],
            )
            assert dump.returncode == 0, "pg_dump failed: " + dump.stderr[-1000:]
            assert dump_path.is_file() and dump_path.stat().st_size > 0

            async with source_sessions() as session, session.begin():
                preview = await create_deletion_preview(session, owner_id=deleted_owner, now=at)
                await confirm_deletion_preview(
                    session,
                    owner_id=deleted_owner,
                    preview_id=preview.id,
                    expected_version=preview.version,
                    now=at,
                )
            assert (
                await finish_pending_deletions(
                    source_sessions, artifact=ledger_path, now=at, purge_owner=purge_account
                )
                == 1
            )
            transport.finish.set()
            assert await asyncio.wait_for(worker, timeout=20)
            assert len(transport.sent) == 1
            async with source_sessions() as session:
                latest = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == intent_id
                    )
                )
                assert latest is not None and latest.outcome == "unknown"

            restore = await asyncio.to_thread(
                _run,
                [
                    str(restore_tool),
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-privileges",
                    f"--dbname={_plain_libpq_url(target_url)}",
                    str(dump_path),
                ],
            )
            assert restore.returncode == 0, "pg_restore failed: " + restore.stderr[-1000:]
            target_engine = create_engine(target_url.render_as_string(hide_password=True))
            target_sessions = create_session_factory(target_engine)

            async with target_sessions() as session:
                stale_plan = await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == deleted_owner)
                )
                assert stale_plan is not None, "snapshot must predate deletion"
                started = await session.scalar(
                    select(DeliveryAttemptRecord).where(
                        DeliveryAttemptRecord.intent_id == intent_id
                    )
                )
                assert started is not None and started.outcome == "started"

            # Validate missing and malformed artifacts fail closed before any worker runs.
            cli_env = os.environ.copy()
            cli_env["PIN_DATABASE_URL"] = target_url.render_as_string(hide_password=True)
            missing = await asyncio.to_thread(
                _run,
                [
                    sys.executable,
                    "-m",
                    "pin.entrypoints.retention_maintenance",
                    "replay",
                    "--artifact",
                    str(tmp_path / "missing.jsonl"),
                ],
                env=cli_env,
            )
            assert missing.returncode == 1 and '"completed": false' in missing.stdout
            valid_ledger = ledger_path.read_bytes()
            ledger_path.write_bytes(valid_ledger + b"malformed-json\n")
            malformed = await asyncio.to_thread(
                _run,
                [
                    sys.executable,
                    "-m",
                    "pin.entrypoints.retention_maintenance",
                    "replay",
                    "--artifact",
                    str(ledger_path),
                ],
                env=cli_env,
            )
            assert malformed.returncode == 1 and '"completed": false' in malformed.stdout
            async with target_sessions() as session:
                assert await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == deleted_owner)
                ) is not None
            ledger_path.write_bytes(valid_ledger)

            replay = await asyncio.to_thread(
                _run,
                [
                    sys.executable,
                    "-m",
                    "pin.entrypoints.retention_maintenance",
                    "replay",
                    "--artifact",
                    str(ledger_path),
                ],
                env=cli_env,
            )
            assert replay.returncode == 0, "retention replay failed: " + replay.stdout[-1000:]
            assert json.loads(replay.stdout)["replayed_tombstones"] == 1
            repeated = await asyncio.to_thread(
                _run,
                [
                    sys.executable,
                    "-m",
                    "pin.entrypoints.retention_maintenance",
                    "replay",
                    "--artifact",
                    str(ledger_path),
                ],
                env=cli_env,
            )
            assert repeated.returncode == 0
            assert json.loads(repeated.stdout)["replayed_tombstones"] == 1

            async with target_sessions() as session:
                deleted = await session.get(AccessAccountRecord, deleted_owner)
                assert deleted is not None and deleted.deleted_at is not None
                assert await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == deleted_owner)
                ) is None
                assert await session.get(TrialActivationRecord, deleted_owner) is not None
                assert await session.get(
                    AccessPeriodRecord, (deleted_owner, "retained-paid-period")
                ) is not None
                assert await session.get(RetainedBillingIdentityRecord, deleted_owner) is not None
                assert await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == send_owner)
                ) is not None

            # Only after successful tombstone replay, expire the restored claim and
            # let the real delivery worker convert its pre-snapshot started attempt.
            async with target_sessions() as session, session.begin():
                await session.execute(
                    update(Job)
                    .where(Job.deduplication_key == f"reminder:{intent_id}:1")
                    .values(lease_until=func.clock_timestamp() - timedelta(seconds=1))
                )
            recovery_transport = _UncertainSendTransport()
            recovery_transport.finish.set()
            recovered = await process_delivery_iteration(
                target_sessions,
                recovery_transport,
                worker_id=f"restore-target-{uuid4().hex[:8]}",
                now=lambda: at + timedelta(minutes=10),
            )
            assert recovered
            assert recovery_transport.sent == []
            async with target_sessions() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(DeliveryAttemptRecord).where(
                                DeliveryAttemptRecord.intent_id == intent_id
                            )
                        )
                    ).all()
                )
                assert len(attempts) == 1 and attempts[0].outcome == "unknown"
        finally:
            transport.finish.set()
            if not worker.done():
                await asyncio.gather(worker, return_exceptions=True)

    try:
        run_local_async(scenario)
    finally:
        run_local_async(source_engine.dispose)
        if target_engine is not None:
            run_local_async(target_engine.dispose)

