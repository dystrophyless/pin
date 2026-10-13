from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
    select,
    text,
    update,
)
from sqlalchemy import (
    create_engine as create_sync_engine,
)
from sqlalchemy.engine import make_url

from pin.contexts.billing.api import SqlAlchemyAccessUsageStore
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.api import (
    ConfirmationGrant,
    ConfirmationProof,
    ConfirmationRejected,
    ConfirmedPlanBatch,
    IdempotencyConflict,
    OneOffPlanDraft,
    PlanKind,
    StalePreview,
    create_confirmed_plan_batch,
)
from pin.contexts.planning.application.commands import AccessDenied
from pin.contexts.planning.application.contracts import ConfirmationVerifier
from pin.contexts.planning.domain.plans import payload_digest, resolve_schedule
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.store import SqlAlchemyPlanningStore
from pin.persistence import SqlAlchemyUnitOfWork, run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.models import ActionReceipt
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_PLANNING_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime(2026, 10, 13, 8, tzinfo=UTC)
TEST_SCHEMA = "planning_test"
test_metadata = MetaData()
confirmation_grants = Table(
    "confirmation_grants",
    test_metadata,
    Column("credential", String(120), primary_key=True),
    Column("owner_id", String(36), nullable=False),
    Column("preview_id", String(36), nullable=False),
    Column("preview_version", Integer, nullable=False),
    Column("producer_id", String(120), nullable=False),
    Column("approved_fields_sha256", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("cancelled", Boolean, nullable=False, default=False),
    Column("consumed", Boolean, nullable=False, default=False),
    schema=TEST_SCHEMA,
)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_PLANNING_DATABASE_URL to the fresh owned issue-8 PostgreSQL database",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def planning_database() -> Iterator[str]:
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.username) == ("127.0.0.1", 51017, "pin_test")
    assert url.database and url.database.startswith("pin_issue8_")
    sync_url = url.set(drivername="postgresql+psycopg")
    engine = create_sync_engine(sync_url)
    try:
        with engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            assert directory.rstrip("/").casefold() == DATA_DIRECTORY.casefold()
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    yield DATABASE_URL


class PersistedTestProducer:
    """Test-only trusted producer whose grants live in the caller's UoW."""

    def __init__(self, sessions, *, verified=None, release=None) -> None:
        self._sessions = sessions
        self._verified = verified
        self._release = release

    async def issue(
        self,
        *,
        owner_id: UUID,
        drafts: tuple[OneOffPlanDraft, ...],
        preferences_version: int = 1,
        now: datetime = NOW,
        lifetime: timedelta = timedelta(hours=1),
    ) -> ConfirmationProof:
        proof_id = uuid4()
        credential = f"trusted-test-token:{uuid4().hex}"
        proof = ConfirmationProof(proof_id, 3, credential)
        digest = payload_digest(drafts, expected_preferences_version=preferences_version)
        async with self._sessions() as session, session.begin():
            await session.execute(
                confirmation_grants.insert().values(
                    credential=credential,
                    owner_id=str(owner_id),
                    preview_id=str(proof_id),
                    preview_version=3,
                    producer_id="trusted-test-preview-producer",
                    approved_fields_sha256=digest,
                    issued_at=now,
                    expires_at=now + lifetime,
                    cancelled=False,
                    consumed=False,
                )
            )
        return proof

    def for_session(self, session) -> ConfirmationVerifier:
        return _PersistedTestVerifier(session, self._verified, self._release)

    async def set_state(
        self,
        proof: ConfirmationProof,
        *,
        cancelled: bool | None = None,
        consumed: bool | None = None,
    ) -> None:
        values = {}
        if cancelled is not None:
            values["cancelled"] = cancelled
        if consumed is not None:
            values["consumed"] = consumed
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(confirmation_grants)
                .where(confirmation_grants.c.credential == proof.credential)
                .values(**values)
            )

    async def state(self, proof: ConfirmationProof) -> tuple[bool, bool]:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(confirmation_grants.c.cancelled, confirmation_grants.c.consumed).where(
                        confirmation_grants.c.credential == proof.credential
                    )
                )
            ).one()
            return row.cancelled, row.consumed


class _PersistedTestVerifier(ConfirmationVerifier):
    def __init__(self, session, verified, release) -> None:
        self._session = session
        self._verified = verified
        self._release = release

    async def verify_and_consume(
        self,
        proof: ConfirmationProof,
        *,
        owner_id: UUID,
        approved_fields_sha256: str,
        at: datetime,
    ) -> ConfirmationGrant | None:
        row = (
            (
                await self._session.execute(
                    select(confirmation_grants)
                    .where(confirmation_grants.c.credential == proof.credential)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row.cancelled or row.consumed:
            return None
        if (
            row.owner_id != str(owner_id)
            or row.preview_id != str(proof.preview_id)
            or row.preview_version != proof.preview_version
            or row.producer_id != "trusted-test-preview-producer"
            or row.approved_fields_sha256 != approved_fields_sha256
            or row.issued_at > at
            or row.expires_at <= at
        ):
            return None
        await self._session.execute(
            update(confirmation_grants)
            .where(confirmation_grants.c.credential == proof.credential)
            .values(consumed=True)
        )
        await self._session.flush()
        if self._verified is not None:
            self._verified.set()
        if self._release is not None:
            await self._release.wait()
        return ConfirmationGrant(
            owner_id,
            proof.preview_id,
            proof.preview_version,
            row.producer_id,
            row.approved_fields_sha256,
            row.issued_at,
            row.expires_at,
        )


def _profile(owner_id: UUID, *, version: int = 1) -> PreferencesRecord:
    return PreferencesRecord(
        owner_id=owner_id,
        language="en",
        home_zone="Asia/Almaty",
        reminder_calendar_days=1,
        reminder_elapsed_hours=1,
        quiet_start=time(22),
        quiet_end=time(8),
        review_time=time(20),
        version=version,
    )


async def _seed_owner(
    sessions,
    owner_id: UUID,
    *,
    active: bool = True,
    expired: bool = False,
    version: int = 1,
) -> None:
    async with sessions() as session, session.begin():
        session.add(_profile(owner_id, version=version))
        session.add(AccessAccountRecord(owner_id=owner_id))
        if active or expired:
            starts_at = NOW - timedelta(days=8) if expired else NOW - timedelta(days=1)
            ends_at = NOW if expired else NOW + timedelta(days=6)
            session.add(
                AccessPeriodRecord(
                    owner_id=owner_id,
                    period_id=f"trial-{uuid4().hex}",
                    policy_kind=PeriodKind.TRIAL.value,
                    policy_version="trial-v1",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    ai_turn_limit=250,
                    voice_second_limit=900,
                )
            )


async def _execute(
    sessions, owner_id: UUID, command_: ConfirmedPlanBatch, verifier, *, clock=None, store=None
):
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        session = uow.session
        return await create_confirmed_plan_batch(
            command_,
            authenticated_owner_id=owner_id,
            store=store or SqlAlchemyPlanningStore(session),
            identity=SqlAlchemyIdentityStore(session),
            billing=SqlAlchemyAccessUsageStore(session),
            confirmation_verifier=verifier.for_session(session),
            clock=clock or FixedClock(NOW),
        )


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


class SequenceClock:
    def __init__(self, *times: datetime) -> None:
        self._times = iter(times)

    def now(self) -> datetime:
        return next(self._times)


class FailAfterPersistStore:
    def __init__(self, store) -> None:
        self._store = store

    async def lock_action(self, owner_id: UUID, action_key: str) -> None:
        await self._store.lock_action(owner_id, action_key)

    async def get_receipt(self, owner_id: UUID, action_key: str):
        return await self._store.get_receipt(owner_id, action_key)

    async def persist_confirmed_batch(self, **kwargs):
        await self._store.persist_confirmed_batch(**kwargs)
        raise RuntimeError("simulated failure after planning rows, receipt, and event were flushed")


@pytest.fixture(scope="session")
def planning_sessions(planning_database: str):
    engine = create_engine(planning_database)
    sessions = create_session_factory(engine)
    sync_engine = create_sync_engine(make_url(planning_database))
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"'))
    test_metadata.create_all(sync_engine)
    yield sessions
    with sync_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
    sync_engine.dispose()
    run_local_async(engine.dispose)


def test_postgres_batch_receipt_and_outbox_commit_once_for_concurrent_replay(
    planning_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        drafts = (
            OneOffPlanDraft(
                PlanKind.APPOINTMENT,
                "Check-up",
                date(2026, 10, 16),
                time(14),
                duration_seconds=3600,
            ),
            OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 15)),
        )
        proof = await verifier.issue(owner_id=owner_id, drafts=drafts)
        command_ = ConfirmedPlanBatch("issue8-concurrent", 1, drafts, proof)
        results = await asyncio.gather(
            _execute(planning_sessions, owner_id, command_, verifier),
            _execute(planning_sessions, owner_id, command_, verifier),
        )
        assert results[0] == results[1]
        plan_ids = [UUID(item["plan_item_id"]) for item in results[0]["plans"]]
        occurrence_ids = [UUID(item["occurrence_id"]) for item in results[0]["plans"]]
        async with planning_sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PlanItemRecord)
                    .where(PlanItemRecord.id.in_(plan_ids), PlanItemRecord.owner_id == owner_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OccurrenceRecord)
                    .where(
                        OccurrenceRecord.id.in_(occurrence_ids),
                        OccurrenceRecord.owner_id == owner_id,
                    )
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id.in_(occurrence_ids),
                        OutboxEvent.owner_id == owner_id,
                    )
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ActionReceipt)
                    .where(
                        ActionReceipt.owner_id == owner_id,
                        ActionReceipt.action_key == "issue8-concurrent",
                    )
                )
                == 1
            )
            created_event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == occurrence_ids[0])
            )
            assert created_event is not None
            assert (
                created_event.event_type,
                created_event.schema_version,
                created_event.aggregate_version,
            ) == (
                "planning.plan_created",
                1,
                1,
            )
            assert created_event.payload["payload"]["event_zone"] == "Asia/Almaty"
            assert created_event.payload["payload"]["starts_at"] is not None

        changed = (replace(drafts[0], title="Different appointment"), drafts[1])
        conflict = ConfirmedPlanBatch(
            command_.action_key,
            1,
            changed,
            await verifier.issue(owner_id=owner_id, drafts=changed),
        )
        with pytest.raises(IdempotencyConflict):
            await _execute(planning_sessions, owner_id, conflict, verifier)

    run_local_async(scenario)


def test_persisted_confirmation_consumption_rolls_back_with_failed_command(
    planning_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        producer = PersistedTestProducer(planning_sessions)
        drafts = (OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 15)),)
        proof = await producer.issue(owner_id=owner_id, drafts=drafts)
        command_ = ConfirmedPlanBatch("issue8-proof-rollback", 1, drafts, proof)

        with pytest.raises(RuntimeError, match="simulated failure"):
            async with SqlAlchemyUnitOfWork(planning_sessions) as uow:
                store = FailAfterPersistStore(SqlAlchemyPlanningStore(uow.session))
                await create_confirmed_plan_batch(
                    command_,
                    authenticated_owner_id=owner_id,
                    store=store,
                    identity=SqlAlchemyIdentityStore(uow.session),
                    billing=SqlAlchemyAccessUsageStore(uow.session),
                    confirmation_verifier=producer.for_session(uow.session),
                    clock=FixedClock(NOW),
                )

        assert await producer.state(proof) == (False, False)
        async with planning_sessions() as session:
            await _assert_no_command_fragments(session, owner_id, command_.action_key)

        # The exact same persisted proof remains usable because proof consumption
        # and the command fragments shared the failed UoW.
        result = await _execute(planning_sessions, owner_id, command_, producer)
        assert len(result["plans"]) == 1
        assert await producer.state(proof) == (False, True)

    run_local_async(scenario)


async def _assert_no_command_fragments(session, owner_id: UUID, action_key: str) -> None:
    for model in (PlanItemRecord, OccurrenceRecord, ReminderRuleRecord):
        assert (
            await session.scalar(
                select(func.count()).select_from(model).where(model.owner_id == owner_id)
            )
            == 0
        )
    assert (
        await session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.owner_id == owner_id)
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(ActionReceipt)
            .where(ActionReceipt.owner_id == owner_id, ActionReceipt.action_key == action_key)
        )
        == 0
    )


def test_mixed_invalid_batch_rolls_back_every_planning_fragment(planning_sessions) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        drafts = (
            OneOffPlanDraft(PlanKind.UNTIMED, "Valid", date(2026, 10, 15)),
            OneOffPlanDraft(PlanKind.TASK, "Past", date(2026, 10, 13), time(7), "Asia/Almaty"),
        )
        command_ = ConfirmedPlanBatch(
            "issue8-invalid-batch",
            1,
            drafts,
            await verifier.issue(owner_id=owner_id, drafts=drafts),
        )
        with pytest.raises(ValueError, match="past"):
            await _execute(planning_sessions, owner_id, command_, verifier)
        async with planning_sessions() as session:
            for model in (
                PlanItemRecord,
                OccurrenceRecord,
                ReminderRuleRecord,
                OutboxEvent,
                ActionReceipt,
            ):
                count = await session.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == owner_id)
                    if hasattr(model, "owner_id")
                    else select(func.count())
                    .select_from(model)
                    .where(OutboxEvent.owner_id == owner_id)
                )
                assert count == 0

    run_local_async(scenario)


@pytest.mark.parametrize(
    "proof_state", ["missing", "forged", "cancelled", "expired", "wrong_owner"]
)
def test_untrusted_or_stale_confirmation_never_persists_plan(
    planning_sessions, proof_state: str
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        drafts = (OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 15)),)
        proof = await verifier.issue(owner_id=owner_id, drafts=drafts)
        if proof_state == "missing":
            async with planning_sessions() as session, session.begin():
                await session.execute(
                    confirmation_grants.delete().where(
                        confirmation_grants.c.credential == proof.credential
                    )
                )
        elif proof_state == "forged":
            proof = ConfirmationProof(proof.preview_id, proof.preview_version, "forged")
        elif proof_state == "cancelled":
            await verifier.set_state(proof, cancelled=True)
        elif proof_state == "expired":
            proof = await verifier.issue(
                owner_id=owner_id,
                drafts=drafts,
                now=NOW - timedelta(days=2),
                lifetime=timedelta(hours=1),
            )
        elif proof_state == "wrong_owner":
            proof = await verifier.issue(owner_id=uuid4(), drafts=drafts)
        command_ = ConfirmedPlanBatch(f"issue8-proof-{proof_state}", 1, drafts, proof)
        with pytest.raises(ConfirmationRejected):
            await _execute(planning_sessions, owner_id, command_, verifier)
        async with planning_sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PlanItemRecord)
                    .where(PlanItemRecord.owner_id == owner_id)
                )
                == 0
            )

    run_local_async(scenario)


def test_access_expiry_while_verification_waits_rolls_back_proof_and_command(
    planning_sessions,
) -> None:
    async def scenario() -> None:
        drafts = (OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 15)),)
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        command_ = ConfirmedPlanBatch(
            "issue8-expired-access",
            1,
            drafts,
            await verifier.issue(owner_id=owner_id, drafts=drafts, lifetime=timedelta(days=30)),
        )
        async with planning_sessions() as session, session.begin():
            await session.execute(
                update(AccessPeriodRecord)
                .where(AccessPeriodRecord.owner_id == owner_id)
                .values(ends_at=NOW + timedelta(days=6))
            )
        with pytest.raises(AccessDenied):
            await _execute(
                planning_sessions,
                owner_id,
                command_,
                verifier,
                clock=SequenceClock(NOW, NOW + timedelta(days=7)),
            )
        assert await verifier.state(command_.proof) == (False, False)
        async with planning_sessions() as session:
            await _assert_no_command_fragments(session, owner_id, command_.action_key)

    run_local_async(scenario)


def test_schedule_that_passes_while_verification_waits_is_rejected(planning_sessions) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        drafts = (
            OneOffPlanDraft(
                PlanKind.TASK,
                "Submit form",
                date(2026, 10, 13),
                time(17, 30),
                "Asia/Almaty",
                reminders=(),
            ),
        )
        command_ = ConfirmedPlanBatch(
            "issue8-schedule-past-at-write",
            1,
            drafts,
            await verifier.issue(owner_id=owner_id, drafts=drafts, lifetime=timedelta(days=2)),
        )
        with pytest.raises(ValueError, match="in the past"):
            await _execute(
                planning_sessions,
                owner_id,
                command_,
                verifier,
                clock=SequenceClock(NOW, NOW + timedelta(hours=6)),
            )
        assert await verifier.state(command_.proof) == (False, False)
        async with planning_sessions() as session:
            await _assert_no_command_fragments(session, owner_id, command_.action_key)

    run_local_async(scenario)


def test_preferences_changed_while_verification_waits_rolls_back_proof_and_command(
    planning_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verified = asyncio.Event()
        release = asyncio.Event()
        verifier = PersistedTestProducer(planning_sessions, verified=verified, release=release)
        drafts = (OneOffPlanDraft(PlanKind.UNTIMED, "Exercise", date(2026, 10, 15)),)
        command_ = ConfirmedPlanBatch(
            "issue8-stale-profile",
            1,
            drafts,
            await verifier.issue(owner_id=owner_id, drafts=drafts),
        )
        applying = asyncio.create_task(_execute(planning_sessions, owner_id, command_, verifier))
        await asyncio.wait_for(verified.wait(), timeout=5)
        async with planning_sessions() as session, session.begin():
            await session.execute(
                update(PreferencesRecord)
                .where(PreferencesRecord.owner_id == owner_id)
                .values(version=2)
            )
        release.set()
        with pytest.raises(StalePreview):
            await applying
        assert await verifier.state(command_.proof) == (False, False)
        async with planning_sessions() as session:
            await _assert_no_command_fragments(session, owner_id, command_.action_key)

    run_local_async(scenario)


def test_reschedule_storage_primitive_preserves_occurrence_identity_and_versions_event(
    planning_sessions,
) -> None:
    async def scenario() -> None:
        owner_id = uuid4()
        await _seed_owner(planning_sessions, owner_id)
        verifier = PersistedTestProducer(planning_sessions)
        drafts = (
            OneOffPlanDraft(
                PlanKind.APPOINTMENT,
                "Check-up",
                date(2026, 10, 16),
                time(14),
                duration_seconds=3600,
            ),
        )
        command_ = ConfirmedPlanBatch(
            "issue8-reschedule-origin",
            1,
            drafts,
            await verifier.issue(owner_id=owner_id, drafts=drafts),
        )
        result = await _execute(planning_sessions, owner_id, command_, verifier)
        occurrence_id = UUID(result["plans"][0]["occurrence_id"])
        new_schedule = resolve_schedule(
            replace(drafts[0], local_date=date(2026, 10, 17)),
            default_zone="Asia/Almaty",
            now=NOW,
        )
        async with SqlAlchemyUnitOfWork(planning_sessions) as uow:
            updated = await SqlAlchemyPlanningStore(uow.session)._reschedule_one_off_occurrence(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                expected_version=1,
                schedule=new_schedule,
                at=NOW,
            )
        assert updated.id == occurrence_id
        assert updated.version == 2
        async with planning_sessions() as session:
            persisted = await session.get(OccurrenceRecord, occurrence_id)
            assert persisted is not None
            assert persisted.id == occurrence_id
            assert persisted.version == 2
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent)
                        .where(OutboxEvent.aggregate_id == occurrence_id)
                        .order_by(OutboxEvent.aggregate_version)
                    )
                ).all()
            )
            assert [(event.event_type, event.aggregate_version) for event in events] == [
                ("planning.plan_created", 1),
                ("planning.plan_rescheduled", 2),
            ]

    run_local_async(scenario)
