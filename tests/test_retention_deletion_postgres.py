from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.adapters.retention.deletion import (
    TEXT_RETENTION_PURGERS,
    finish_pending_deletions,
    purge_expired_text_once,
    replay_deletion_artifact,
)
from pin.adapters.retention.maintenance import run_retention_once
from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.deletion import purge_account
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.accountability.infrastructure.delivery_events import consume_delivery_outcomes
from pin.contexts.accountability.infrastructure.models import CheckInCycleRecord
from pin.contexts.accountability.infrastructure.planning_events import consume_planning_cycle_events
from pin.contexts.billing.api import (
    AccountLifecycle,
    RestoreReturningPaidAccess,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    RetainedBillingIdentityRecord,
    TrialActivationRecord,
)
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureDraftRecord,
    CaptureInputRecord,
)
from pin.contexts.notifications.infrastructure.delivery_worker import (
    DELIVERY_REPLY_JOB_KIND,
    deliver_reply_job,
    enqueue_due_reply_jobs,
)
from pin.contexts.notifications.infrastructure.outbox_consumer import (
    SqlAlchemyNotificationScheduleEventConsumer,
)
from pin.contexts.notifications.infrastructure.store import SqlAlchemyNotificationsStore
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.contexts.planning.infrastructure.mutation_models import MutationPreviewRecord
from pin.contexts.retention.domain.deletion import (
    DELETION_PREVIEW_TTL_SECONDS,
    DeletionRejected,
    DeletionState,
    DeletionTombstone,
)
from pin.contexts.retention.infrastructure.ledger import append_tombstones
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
)
from pin.contexts.retention.infrastructure.store import (
    cancel_deletion_preview,
    confirm_deletion_preview,
    create_deletion_preview,
)
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.persistence.models import ActionReceipt, Base
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobLane, claim_job
from pin.platform.models import InboxUpdate, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "PIN_TEST_RETENTION_DATABASE_URL"
DATABASE_PREFIX = "pin_issue23_retention_"
DATABASE_PORT = 51017
DATABASE_USER = "pin_test"
DATABASE_VERSION = "18.6"
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"


def _guarded_database_url() -> str:
    value = os.getenv(DATABASE_ENV)
    if not value:
        pytest.skip(f"set {DATABASE_ENV} to the assigned fresh retention fixture")
    url = make_url(value)
    if (
        url.host != "127.0.0.1"
        or url.port != DATABASE_PORT
        or url.username != DATABASE_USER
        or url.database is None
        or not url.database.startswith(DATABASE_PREFIX)
        or not url.database.removeprefix(DATABASE_PREFIX).replace("_", "").isalnum()
    ):
        pytest.fail("retention PostgreSQL URL does not match the assigned fixture identity")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            version = str(connection.exec_driver_sql("show server_version").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
            has_migrations = connection.exec_driver_sql(
                "select to_regclass('public.alembic_version') is not null"
            ).scalar_one()
    finally:
        engine.dispose()
    if (
        name != url.database
        or port != DATABASE_PORT
        or user != DATABASE_USER
        or version != DATABASE_VERSION
        or directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold()
    ):
        pytest.fail("connected PostgreSQL server does not match the assigned retention fixture")
    if not has_migrations:
        config = Config(ROOT / "alembic.ini")
        config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
        command.upgrade(config, "head")
    return value


@pytest.fixture(scope="module")
def retention_database_url() -> str:
    return _guarded_database_url()


@pytest.fixture(scope="module")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture(scope="module")
async def sessions(
    retention_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(retention_database_url)
    try:
        yield async_sessionmaker[AsyncSession](engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _clean_owner(sessions: async_sessionmaker[AsyncSession], owner_id: UUID) -> None:
    async with sessions() as session, session.begin():
        for table in reversed(Base.metadata.sorted_tables):
            if table.schema and "owner_id" in table.c:
                await session.execute(delete(table).where(table.c.owner_id == owner_id))


async def _seed_account(
    sessions: async_sessionmaker[AsyncSession], owner_id: UUID, *, paid_until: datetime
) -> None:
    async with sessions() as session, session.begin():
        session.add(AccessAccountRecord(owner_id=owner_id))
        telegram_user_id = abs(owner_id.int % 2_000_000_000) + 1
        session.add(
            RetainedBillingIdentityRecord(
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                retained_at=paid_until - timedelta(days=30),
            )
        )
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id="paid-period",
                policy_kind="paid",
                policy_version="test-policy-v1",
                starts_at=paid_until - timedelta(days=30),
                ends_at=paid_until,
                ai_turn_limit=1000,
                voice_second_limit=3600,
            )
        )
        session.add(
            TrialActivationRecord(
                owner_id=owner_id,
                telegram_user_id=telegram_user_id,
                period_id="trial-period-used",
                activated_at=paid_until - timedelta(days=90),
            )
        )


async def _dispatch_telegram_update(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    update: dict[str, object],
    now: datetime,
) -> tuple[UUID, dict[str, object]]:
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload=update,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
    claim = await claim_application_job(sessions, worker_id=f"retention-app-{uuid4().hex[:8]}")
    assert claim is not None
    await process_application_job(sessions, claim, now=now)
    async with sessions() as session:
        reply = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert reply is not None
        assert isinstance(reply.payload.get("payload"), dict)
        return claim.owner_id, dict(reply.payload["payload"])


@pytest.mark.asyncio
async def test_preview_is_owner_bound_current_version_and_idempotent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id, other_owner_id = uuid4(), uuid4()
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    await _seed_account(sessions, owner_id, paid_until=now + timedelta(days=20))
    try:
        async with sessions() as session, session.begin():
            preview = await create_deletion_preview(session, owner_id=owner_id, now=now)
            assert preview.owner_id == owner_id
            assert preview.expires_at == now + timedelta(seconds=DELETION_PREVIEW_TTL_SECONDS)
            with pytest.raises(DeletionRejected):
                await cancel_deletion_preview(
                    session,
                    owner_id=other_owner_id,
                    preview_id=preview.id,
                    expected_version=preview.version,
                    now=now,
                )
            with pytest.raises(DeletionRejected):
                await confirm_deletion_preview(
                    session,
                    owner_id=owner_id,
                    preview_id=preview.id,
                    expected_version=preview.version + 1,
                    now=now,
                )
            assert await cancel_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=preview.id,
                expected_version=preview.version,
                now=now,
            )
            assert not await cancel_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=preview.id,
                expected_version=preview.version,
                now=now,
            )
            current = await create_deletion_preview(session, owner_id=owner_id, now=now)
            tombstone = await confirm_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=current.id,
                expected_version=current.version,
                now=now,
            )
            repeated = await confirm_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=current.id,
                expected_version=current.version,
                now=now,
            )
            assert repeated == tombstone
            billing = SqlAlchemyAccessUsageStore(session)
            assert await billing.lock_account_lifecycle(owner_id) is AccountLifecycle.DELETED
            assert not await billing.restore_returning_paid_access(
                RestoreReturningPaidAccess(
                    owner_id,
                    abs(owner_id.int % 2_000_000_000) + 1,
                    now,
                )
            )
    finally:
        await _clean_owner(sessions, owner_id)
        await _clean_owner(sessions, other_owner_id)


@pytest.mark.asyncio
async def test_delete_export_replay_and_tr03_return_preserve_new_generation(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner_id = uuid4()
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    ledger = tmp_path / "deletions.jsonl"
    await _seed_account(sessions, owner_id, paid_until=now + timedelta(days=20))
    try:
        async with sessions() as session, session.begin():
            session.add(
                PlanItemRecord(
                    owner_id=owner_id,
                    kind="task",
                    title="old plan must be deleted",
                )
            )
            preview = await create_deletion_preview(session, owner_id=owner_id, now=now)
            tombstone = await confirm_deletion_preview(
                session,
                owner_id=owner_id,
                preview_id=preview.id,
                expected_version=preview.version,
                now=now,
            )

        assert (
            await finish_pending_deletions(
                sessions, artifact=ledger, now=now, purge_owner=purge_account
            )
            == 1
        )
        assert ledger.exists()
        assert str(owner_id) in ledger.read_text(encoding="utf-8")
        async with sessions() as session, session.begin():
            assert (
                await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
                )
                is None
            )
            assert await session.get(TrialActivationRecord, owner_id) is not None
            paid = await session.get(AccessPeriodRecord, (owner_id, "paid-period"))
            assert paid is not None
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None and account.deleted_at is not None
            assert (
                await session.scalar(
                    select(DeletionRequestRecord.state).where(
                        DeletionRequestRecord.owner_id == owner_id
                    )
                )
                == DeletionState.COMPLETED.value
            )
            assert await SqlAlchemyAccessUsageStore(session).restore_returning_paid_access(
                RestoreReturningPaidAccess(
                    owner_id,
                    abs(owner_id.int % 2_000_000_000) + 1,
                    now,
                )
            )
            session.add(
                PlanItemRecord(owner_id=owner_id, kind="task", title="new post-return plan")
            )

        # A later legitimate paid return is part of a newer snapshot. Replaying an
        # already-persisted tombstone must not erase that new generation.
        assert (
            await replay_deletion_artifact(
                sessions, artifact=ledger, now=now + timedelta(days=1), purge_owner=purge_account
            )
            == 1
        )
        async with sessions() as session, session.begin():
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None and account.deleted_at is None
            plan = await session.scalar(
                select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
            )
            assert plan is not None and plan.title == "new post-return plan"
            assert await session.get(TrialActivationRecord, owner_id) is not None
        # A snapshot from before deletion has no local tombstone receipt. The external
        # artifact still fences it and removes its stale plan data.
        stale_owner = uuid4()
        await _seed_account(sessions, stale_owner, paid_until=now + timedelta(days=20))
        stale_tombstone = DeletionTombstone(uuid4(), stale_owner, now)
        append_tombstones(ledger, (stale_tombstone,))
        async with sessions() as session, session.begin():
            session.add(PlanItemRecord(owner_id=stale_owner, kind="task", title="snapshot plan"))
        assert (
            await replay_deletion_artifact(
                sessions, artifact=ledger, now=now + timedelta(days=2), purge_owner=purge_account
            )
            == 2
        )
        async with sessions() as session, session.begin():
            assert (
                await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == stale_owner)
                )
                is None
            )
            assert (
                await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(stale_owner)
                is AccountLifecycle.DELETED
            )
    finally:
        await _clean_owner(sessions, owner_id)
        if "stale_owner" in locals():
            await _clean_owner(sessions, stale_owner)


@pytest.mark.asyncio
async def test_30_day_purge_covers_conversation_speech_and_platform_text(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=29)
    drafts: list[UUID] = []
    cycle_ids: list[UUID] = []
    preview_ids: list[UUID] = []
    try:
        async with sessions() as session, session.begin():
            for created_at, suffix in ((old, "old"), (recent, "recent")):
                draft_id = uuid4()
                drafts.append(draft_id)
                session.add(
                    CaptureDraftRecord(
                        id=draft_id,
                        owner_id=owner_id,
                        version=1,
                        sequence=1 if suffix == "old" else 2,
                        state="cancelled" if suffix == "old" else "collecting",
                        messages=[{"role": "user", "text": f"{suffix} private text"}],
                        created_at=created_at,
                        updated_at=created_at,
                        expires_at=now + timedelta(days=1),
                    )
                )
                session.add(
                    CaptureInputRecord(
                        owner_id=owner_id,
                        draft_id=draft_id,
                        input_key=f"retention-{suffix}-{owner_id}",
                        sequence=1 if suffix == "old" else 2,
                        state="complete",
                        response={"text": f"{suffix} private response"},
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                job_id, inbox_id = uuid4(), uuid4()
                session.add(
                    SpeechRequestRecord(
                        job_id=job_id,
                        owner_id=owner_id,
                        inbox_id=inbox_id,
                        request_key=f"speech-{suffix}-{owner_id}",
                        state="transcribed",
                        declared_duration_seconds=2,
                        transcript=f"{suffix} recognized words",
                        language="en",
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                session.add(
                    InboxUpdate(
                        id=inbox_id,
                        bot_id=7001,
                        update_id=abs(owner_id.int % 2_000_000_000) + (1 if suffix == "old" else 2),
                        owner_id=owner_id,
                        update_type="message",
                        payload={"message": {"text": f"{suffix} inbound private text"}},
                        received_at=created_at,
                    )
                )
                cycle_id = uuid4()
                cycle_ids.append(cycle_id)
                session.add(
                    CheckInCycleRecord(
                        id=cycle_id,
                        owner_id=owner_id,
                        occurrence_id=uuid4(),
                        occurrence_version=1,
                        kind="appointment",
                        title=f"{suffix} copied plan title",
                        expected_end_at=created_at,
                        state="open",
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                session.add(
                    ActionReceipt(
                        owner_id=owner_id,
                        action_key=f"retention-{suffix}-{owner_id}",
                        result={"title": f"{suffix} retained command result"},
                        created_at=created_at,
                    )
                )
                plan_id, occurrence_id, preview_id = uuid4(), uuid4(), uuid4()
                preview_ids.append(preview_id)
                session.add(
                    PlanItemRecord(
                        id=plan_id,
                        owner_id=owner_id,
                        kind="task",
                        title=f"{suffix} retained plan title",
                    )
                )
                await session.flush()
                session.add(
                    OccurrenceRecord(
                        id=occurrence_id,
                        owner_id=owner_id,
                        plan_item_id=plan_id,
                        kind="task",
                        event_zone="UTC",
                        local_date=created_at.date(),
                        local_time=datetime.min.time().replace(hour=9),
                        starts_at=created_at,
                        state="open",
                        version=1,
                    )
                )
                session.add(
                    MutationPreviewRecord(
                        id=preview_id,
                        owner_id=owner_id,
                        occurrence_id=occurrence_id,
                        occurrence_version=1,
                        preferences_version=1,
                        preview_version=1,
                        action="complete",
                        scope="single",
                        projection_sha256="a" * 64,
                        payload={"title": f"{suffix} preview title"},
                        state="pending",
                        created_at=created_at,
                        expires_at=created_at + timedelta(hours=1),
                        updated_at=created_at,
                    )
                )

        removed = await purge_expired_text_once(
            sessions,
            now=now,
            purgers=TEXT_RETENTION_PURGERS,
        )
        assert removed >= 7
        async with sessions() as session:
            assert await session.get(CaptureDraftRecord, drafts[0]) is None
            assert await session.get(CaptureDraftRecord, drafts[1]) is not None
            assert await session.get(CheckInCycleRecord, cycle_ids[0]) is None
            assert await session.get(CheckInCycleRecord, cycle_ids[1]) is not None
            assert await session.get(MutationPreviewRecord, preview_ids[0]) is None
            assert await session.get(MutationPreviewRecord, preview_ids[1]) is not None
            assert (
                await session.scalar(
                    select(PlanItemRecord.id).where(
                        PlanItemRecord.owner_id == owner_id,
                        PlanItemRecord.title == "old retained plan title",
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(ActionReceipt.id).where(
                        ActionReceipt.owner_id == owner_id,
                        ActionReceipt.action_key == f"retention-old-{owner_id}",
                    )
                )
                is None
            )
            assert (
                await session.scalar(
                    select(ActionReceipt.id).where(
                        ActionReceipt.owner_id == owner_id,
                        ActionReceipt.action_key == f"retention-recent-{owner_id}",
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(SpeechRequestRecord.job_id).where(
                        SpeechRequestRecord.owner_id == owner_id,
                        SpeechRequestRecord.state == "transcribed",
                        SpeechRequestRecord.created_at < now - timedelta(days=30),
                    )
                )
                is None
            )
            assert (
                await session.scalar(
                    select(SpeechRequestRecord.job_id).where(
                        SpeechRequestRecord.owner_id == owner_id,
                        SpeechRequestRecord.created_at >= now - timedelta(days=30),
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(InboxUpdate.id).where(
                        InboxUpdate.owner_id == owner_id,
                        InboxUpdate.received_at < now - timedelta(days=30),
                    )
                )
                is None
            )
            assert (
                await session.scalar(
                    select(InboxUpdate.id).where(
                        InboxUpdate.owner_id == owner_id,
                        InboxUpdate.received_at >= now - timedelta(days=30),
                    )
                )
                is not None
            )
    finally:
        await _clean_owner(sessions, owner_id)


@pytest.mark.asyncio
async def test_stale_schedule_outbox_cannot_recreate_deleted_owner_state(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = uuid4()
    event_id = uuid4()
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    try:
        async with sessions() as session, session.begin():
            session.add(AccessAccountRecord(owner_id=owner_id, deleted_at=now))
            session.add(
                OutboxEvent(
                    id=event_id,
                    event_type="planning.plan_created",
                    schema_version=1,
                    aggregate_id=owner_id,
                    aggregate_version=1,
                    owner_id=owner_id,
                    occurred_at=now,
                    payload={
                        "id": str(event_id),
                        "event_type": "planning.plan_created",
                        "schema_version": 1,
                        "aggregate_id": str(owner_id),
                        "aggregate_version": 1,
                        "owner_id": str(owner_id),
                        "occurred_at": now.isoformat(),
                        "payload": {},
                    },
                )
            )
        async with sessions() as session, session.begin():
            consumer = SqlAlchemyNotificationScheduleEventConsumer(
                session, SqlAlchemyNotificationsStore(session)
            )
            assert await consumer.consume_pending(at=now) == 0
            assert (
                await session.scalar(select(OutboxEvent.id).where(OutboxEvent.id == event_id))
                == event_id
            )
    finally:
        await _clean_owner(sessions, owner_id)


@pytest.mark.asyncio
async def test_outbox_consumers_lock_oppositely_ordered_owners_consistently(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    low_owner, high_owner = sorted((uuid4(), uuid4()), key=lambda owner: owner.int)
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    try:
        async with sessions() as session, session.begin():
            for event_type in (
                "planning.plan_created",
                "notifications.delivery_attempt_outcome",
            ):
                # Candidate event order is intentionally high owner then low owner.
                for offset, owner_id in enumerate((high_owner, low_owner)):
                    event_id = uuid4()
                    occurred_at = now + timedelta(seconds=offset)
                    envelope = EventEnvelope(
                        id=event_id,
                        event_type=event_type,
                        schema_version=1,
                        aggregate_id=uuid4(),
                        aggregate_version=1,
                        owner_id=owner_id,
                        occurred_at=occurred_at,
                        payload={},
                    )
                    await add_outbox_event(session, envelope)
                    await session.flush()
                    event_record = await session.get(OutboxEvent, event_id)
                    assert event_record is not None
                    event_record.created_at = occurred_at

        lifecycle_locks: list[UUID] = []

        async def record_lifecycle_lock(_store, owner_id: UUID) -> AccountLifecycle:
            lifecycle_locks.append(owner_id)
            return AccountLifecycle.PRESENT

        monkeypatch.setattr(
            SqlAlchemyAccessUsageStore,
            "lock_account_lifecycle",
            record_lifecycle_lock,
        )

        class NoCurrentOccurrence:
            async def get_current_occurrence(self, _owner_id: UUID, _occurrence_id: UUID):
                return None

        async with sessions() as session, session.begin():
            assert (
                await consume_planning_cycle_events(
                    session,
                    current_occurrences=NoCurrentOccurrence(),
                    at=now,
                )
                == 2
            )
        expected_order = [low_owner, high_owner]
        assert lifecycle_locks == expected_order

        lifecycle_locks.clear()
        async with sessions() as session, session.begin():
            assert await consume_delivery_outcomes(session, at=now) == 2
        assert lifecycle_locks == expected_order
    finally:
        await _clean_owner(sessions, low_owner)
        await _clean_owner(sessions, high_owner)


@pytest.mark.asyncio
async def test_telegram_delete_account_callbacks_and_scheduled_maintenance(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    class LocalReplyTransport:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def resolve_destination(
            self, session: AsyncSession, target_owner: UUID
        ) -> int | None:
            return await session.scalar(
                select(TelegramOwnerRecord.telegram_user_id).where(
                    TelegramOwnerRecord.owner_id == target_owner
                )
            )

        async def send_reply(self, message: object) -> str:
            self.sent.append(message)
            return "local-mock-message"

    owner_id = uuid4()
    now = datetime.now(UTC)
    bot_id = 73001
    telegram_user_id = abs(owner_id.int % 2_000_000_000) + 1
    artifact = tmp_path / "deletion-ledger.jsonl"
    await _seed_account(sessions, owner_id, paid_until=now + timedelta(days=20))
    try:
        async with sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    owner_id=owner_id,
                )
            )
            session.add(
                PlanItemRecord(owner_id=owner_id, kind="task", title="must purge after confirm")
            )

        async def message(update_id: int, text: str) -> dict[str, object]:
            return {
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "chat": {"id": telegram_user_id, "type": "private"},
                    "from": {"id": telegram_user_id, "is_bot": False},
                    "text": text,
                },
            }

        async def callback(update_id: int, token: str) -> dict[str, object]:
            return {
                "update_id": update_id,
                "callback_query": {
                    "id": f"retention-callback-{update_id}",
                    "from": {"id": telegram_user_id, "is_bot": False},
                    "message": {
                        "message_id": update_id,
                        "chat": {"id": telegram_user_id, "type": "private"},
                    },
                    "data": token,
                },
            }

        resolved_owner, preview_reply = await _dispatch_telegram_update(
            sessions, bot_id=bot_id, update=await message(91_001, "/delete_account"), now=now
        )
        assert resolved_owner == owner_id
        keyboard = preview_reply["reply_markup"]["inline_keyboard"]
        cancel_token = keyboard[0][1]["callback_data"]
        assert isinstance(cancel_token, str)
        _, cancel_reply = await _dispatch_telegram_update(
            sessions,
            bot_id=bot_id,
            update=await callback(91_002, cancel_token),
            now=now,
        )
        assert "cancelled" in str(cancel_reply["plain_text"]).casefold()
        async with sessions() as session:
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None and account.deleted_at is None

        # Exercise the durable reply delivery lane with a local transport only.
        transport = LocalReplyTransport()
        delivery_now = datetime.now(UTC) + timedelta(seconds=1)
        async with sessions() as session, session.begin():
            assert await enqueue_due_reply_jobs(session, now=delivery_now) == 2
        for _ in range(2):
            async with sessions() as session, session.begin():
                claim = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id=f"retention-delivery-{uuid4().hex[:8]}",
                    lease_for=timedelta(seconds=45),
                )
                assert claim is not None and claim.kind == DELIVERY_REPLY_JOB_KIND
            await deliver_reply_job(sessions, claim, transport, now=lambda: delivery_now)
        assert len(transport.sent) == 2

        _, second_preview = await _dispatch_telegram_update(
            sessions, bot_id=bot_id, update=await message(91_003, "/delete_account"), now=now
        )
        second_keyboard = second_preview["reply_markup"]["inline_keyboard"]
        confirm_token = second_keyboard[0][0]["callback_data"]
        assert isinstance(confirm_token, str)
        _, confirmation_reply = await _dispatch_telegram_update(
            sessions,
            bot_id=bot_id,
            update=await callback(91_004, confirm_token),
            now=now,
        )
        assert "confirmed" in str(confirmation_reply["plain_text"]).casefold()
        async with sessions() as session:
            account = await session.get(AccessAccountRecord, owner_id)
            assert account is not None and account.deleted_at == now and account.deletion_pending
            assert (
                await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
                )
                is not None
            )

        # A delivery race after confirmation must not reveal stale reply content.
        async with sessions() as session, session.begin():
            assert (
                await enqueue_due_reply_jobs(session, now=datetime.now(UTC) + timedelta(seconds=1))
                == 2
            )
        blocked_claims = []
        async with sessions() as session, session.begin():
            for _ in range(2):
                blocked_claim = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id=f"retention-deleted-reply-{uuid4().hex[:8]}",
                    lease_for=timedelta(seconds=45),
                )
                assert blocked_claim is not None and blocked_claim.kind == DELIVERY_REPLY_JOB_KIND
                blocked_claims.append(blocked_claim)
        sent_before_deleted_reply = len(transport.sent)
        for blocked_claim in blocked_claims:
            await deliver_reply_job(
                sessions, blocked_claim, transport, now=lambda: datetime.now(UTC)
            )
        assert len(transport.sent) == sent_before_deleted_reply

        stale_update = await message(91_005, "/today")
        async with sessions() as session, session.begin():
            stale_receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload=stale_update,
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert stale_receipt is not None and stale_receipt.accepted
        stale_claim = await claim_application_job(
            sessions, worker_id=f"retention-stale-app-{uuid4().hex[:8]}"
        )
        assert stale_claim is not None
        stale_inbox_id = UUID(str(stale_claim.payload["inbox_id"]))
        async with sessions() as session, session.begin():
            stale_inbox = await session.get(InboxUpdate, stale_inbox_id)
            assert stale_inbox is not None
            stale_inbox.received_at = now - timedelta(seconds=1)
        await process_application_job(sessions, stale_claim, now=now)
        async with sessions() as session:
            stale_reply = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.causation_id == stale_claim.job_id)
            )
            assert stale_reply is None

        # A new owner-authenticated request after deletion gets only the fixed
        # denial. Old, unmarked reply requests above remain suppressed.
        denied_owner, denial_payload = await _dispatch_telegram_update(
            sessions, bot_id=bot_id, update=await message(91_006, "/today"), now=now
        )
        assert denied_owner == owner_id
        assert denial_payload.get("safe_deleted_account_denial") is True
        async with sessions() as session, session.begin():
            assert await enqueue_due_reply_jobs(session, now=datetime.now(UTC)) == 1
        async with sessions() as session, session.begin():
            denial_claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id=f"retention-denial-delivery-{uuid4().hex[:8]}",
                lease_for=timedelta(seconds=45),
            )
            assert denial_claim is not None and denial_claim.kind == DELIVERY_REPLY_JOB_KIND
        await deliver_reply_job(sessions, denial_claim, transport, now=lambda: datetime.now(UTC))
        assert len(transport.sent) == sent_before_deleted_reply + 1
        assert transport.sent[-1].plain_text == "Account data is unavailable. Данные недоступны."

        result = await run_retention_once(
            sessions,
            artifact=artifact,
            now=now,
            private_files=PrivateAudioFiles(tmp_path / "audio"),
        )
        assert result.completed_deletions == 1
        assert artifact.exists()
        async with sessions() as session:
            account = await session.get(AccessAccountRecord, owner_id)
            assert (
                account is not None and account.deleted_at == now and not account.deletion_pending
            )
            assert (
                await session.scalar(
                    select(PlanItemRecord.id).where(PlanItemRecord.owner_id == owner_id)
                )
                is None
            )
            assert await session.get(TrialActivationRecord, owner_id) is not None
            assert await session.get(AccessPeriodRecord, (owner_id, "paid-period")) is not None
    finally:
        await _clean_owner(sessions, owner_id)
