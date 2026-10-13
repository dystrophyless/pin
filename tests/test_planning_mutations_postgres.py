from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.adapters.telegram.mutations import (
    SqlAlchemyTelegramMutationVerifier,
    mutation_proof_from_callback,
    render_confirmable_mutation,
)
from pin.contexts.billing.domain.access import AccountLifecycle, Operation
from pin.contexts.identity.application.contracts import PreferencesSnapshot
from pin.contexts.planning.domain.mutations import (
    MutationAction,
    MutationRequest,
    MutationScope,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    PlanKind,
    ReminderOffset,
    resolve_reminder_projection,
    resolve_schedule,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
)
from pin.contexts.planning.infrastructure import mutation_store as mutation_store_module
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.mutation_store import SqlAlchemyMutationStore
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.contexts.planning.mutations_api import (
    MutationAccessDenied,
    MutationRejected,
    MutationStalePreview,
    confirm_mutation,
    preview_mutation,
)
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

# psycopg's async implementation requires SelectorEventLoop on Windows. Set the
# policy before pytest-asyncio creates its session/function loops.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_MUTATIONS_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_MUTATIONS_DATABASE_URL to the assigned Issue 15 fixture")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = connection.exec_driver_sql("select current_database()").scalar_one()
            port = connection.exec_driver_sql("show port").scalar_one()
            directory = connection.exec_driver_sql("show data_directory").scalar_one()
    finally:
        engine.dispose()
    if not str(name).startswith(DATABASE_PREFIX):
        pytest.fail("mutation database is outside the assigned pin_issue15_ prefix")
    if int(port) != DATABASE_PORT:
        pytest.fail("mutation database is not on the assigned port")
    if str(directory).replace("\\", "/") != DATA_DIRECTORY:
        pytest.fail("mutation database is outside the assigned disposable PostgreSQL cluster")
    return value


@pytest.fixture(scope="session")
def mutation_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


class _Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class _Identity:
    def __init__(self, owner_id: UUID, *, version: int = 1, home_zone: str = "UTC") -> None:
        self.preferences = PreferencesSnapshot(
            owner_id=owner_id,
            language="en",
            home_zone=home_zone,
            reminder_calendar_days=1,
            reminder_elapsed_hours=1,
            quiet_start=time(22),
            quiet_end=time(7),
            review_time=time(20),
            version=version,
        )

    async def get_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None:
        return self.preferences if owner_id == self.preferences.owner_id else None

    async def lock_preferences(self, owner_id: UUID) -> PreferencesSnapshot | None:
        return await self.get_preferences(owner_id)


class _Billing:
    def __init__(self) -> None:
        self.operations: list[Operation] = []

    async def account_lifecycle(self, _owner_id: UUID) -> AccountLifecycle:
        return AccountLifecycle.PRESENT

    async def lock_account_lifecycle(self, _owner_id: UUID) -> AccountLifecycle:
        return AccountLifecycle.PRESENT

    async def lock_access(self, query):
        self.operations.append(query.operation)
        return type("Decision", (), {"allowed": True})()


class _TestClock(_Clock):
    pass


async def _seed_one_off(
    session: AsyncSession,
    *,
    owner_id: UUID,
    at: datetime,
    day: date,
    reminder_preferences_version: int | None = 1,
    with_reminder: bool = True,
) -> UUID:
    draft = OneOffPlanDraft(
        kind=PlanKind.TASK,
        title="Review the release",
        local_date=day,
        local_time=time(12),
        event_zone="UTC",
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    item_id = uuid4()
    occurrence_id = uuid4()
    session.add(
        PlanItemRecord(
            id=item_id,
            owner_id=owner_id,
            kind="task",
            title=draft.title,
            version=1,
        )
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=item_id,
            kind=schedule.kind.value,
            event_zone=schedule.event_zone,
            local_date=schedule.local_date,
            local_time=schedule.local_time,
            fold=schedule.fold,
            end_local_date=schedule.end_local_date,
            end_local_time=schedule.end_local_time,
            end_fold=schedule.end_fold,
            starts_at=schedule.starts_at,
            ends_at=schedule.ends_at,
            deadline_at=schedule.deadline_at,
            duration_seconds=schedule.duration_seconds,
            state="open",
            version=1,
            reminder_preferences_version=reminder_preferences_version,
        )
    )
    if with_reminder:
        offset = ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "explicit")
        reminder = resolve_reminder_projection(schedule, (offset,), now=at).scheduled[0]
        session.add(
            ReminderRuleRecord(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                offset_kind=offset.kind.value,
                offset_amount=offset.amount,
                source=offset.source,
                due_at=reminder.due_at,
            )
        )
    await session.flush()
    return occurrence_id


async def _confirm(session, preview, owner_id: UUID, identity, billing, clock):
    _message, keyboard = await render_confirmable_mutation(
        session, preview, language="en", now=clock.now()
    )
    proof = mutation_proof_from_callback(
        keyboard.inline_keyboard[0][0].callback_data,
        preview.preview_id,
        preview.preview_version,
    )
    return await confirm_mutation(
        proof,
        authenticated_owner_id=owner_id,
        store=SqlAlchemyMutationStore(session),
        identity=identity,
        billing=billing,
        confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_mutation_preview_is_read_only_and_confirm_completes_once(mutation_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    identity = _Identity(owner_id)
    billing = _Billing()
    clock = _TestClock(at)
    async with mutation_sessions() as session, session.begin():
        occurrence_id = await _seed_one_off(
            session, owner_id=owner_id, at=at, day=date(2026, 10, 20), with_reminder=False
        )
        store = SqlAlchemyMutationStore(session)
        preview = await preview_mutation(
            MutationRequest(owner_id, occurrence_id, 1, MutationAction.COMPLETE),
            authenticated_owner_id=owner_id,
            store=store,
            identity=identity,
            billing=billing,
            clock=clock,
        )
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None and record.state == "open" and record.version == 1
        receipt = await _confirm(session, preview, owner_id, identity, billing, clock)
        assert receipt["state"] == "completed"
        assert await _confirm(session, preview, owner_id, identity, billing, clock) == receipt
        # Preview and first confirmation each recheck current entitlement; the
        # idempotent receipt replay does not invoke Billing again.
        assert billing.operations == [Operation.COMPLETE_PLAN, Operation.COMPLETE_PLAN]
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None and record.state == "completed" and record.version == 2
        with pytest.raises(MutationRejected):
            await preview_mutation(
                MutationRequest(owner_id, occurrence_id, 2, MutationAction.COMPLETE),
                authenticated_owner_id=owner_id,
                store=SqlAlchemyMutationStore(session),
                identity=identity,
                billing=billing,
                clock=clock,
            )
        events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.owner_id == owner_id,
                    OutboxEvent.event_type == "planning.occurrence_completed",
                )
            )
        ).all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_reschedule_confirmation_rejects_started_occurrence(mutation_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    identity = _Identity(owner_id)
    billing = _Billing()
    clock = _TestClock(at)
    async with mutation_sessions() as session, session.begin():
        occurrence_id = await _seed_one_off(
            session, owner_id=owner_id, at=at, day=date(2026, 10, 14), with_reminder=False
        )
        preview = await preview_mutation(
            MutationRequest(
                owner_id,
                occurrence_id,
                1,
                MutationAction.RESCHEDULE,
                replacement=OneOffPlanDraft(
                    kind=PlanKind.TASK,
                    title="Review the release",
                    local_date=date(2026, 10, 15),
                    local_time=time(12),
                    event_zone="UTC",
                ),
            ),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )
        clock.at = datetime(2026, 10, 14, 12, 1, tzinfo=UTC)
        _message, keyboard = await render_confirmable_mutation(
            session, preview, language="en", now=clock.now()
        )
        proof = mutation_proof_from_callback(
            keyboard.inline_keyboard[0][0].callback_data,
            preview.preview_id,
            preview.preview_version,
        )
        with pytest.raises(MutationStalePreview, match="started"):
            await confirm_mutation(
                proof,
                authenticated_owner_id=owner_id,
                store=SqlAlchemyMutationStore(session),
                identity=identity,
                billing=billing,
                confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
                clock=clock,
            )
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None
        assert record.state == "open" and record.local_date == date(2026, 10, 14)


@pytest.mark.asyncio
async def test_reschedule_reapproves_stored_reminders_after_profile_version_change(
    mutation_sessions,
):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    identity = _Identity(owner_id, version=2)
    billing = _Billing()
    clock = _TestClock(at)
    async with mutation_sessions() as session, session.begin():
        occurrence_id = await _seed_one_off(
            session,
            owner_id=owner_id,
            at=at,
            day=date(2026, 10, 20),
            reminder_preferences_version=None,
        )
        request = MutationRequest(
            owner_id,
            occurrence_id,
            1,
            MutationAction.RESCHEDULE,
            replacement=OneOffPlanDraft(
                kind=PlanKind.TASK,
                title="Review the release",
                local_date=date(2026, 10, 22),
                local_time=time(13),
                event_zone=None,
            ),
        )
        store = SqlAlchemyMutationStore(session)
        preview = await preview_mutation(
            request,
            authenticated_owner_id=owner_id,
            store=store,
            identity=identity,
            billing=billing,
            clock=clock,
        )
        assert preview.target.reminder_offsets == (
            ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "explicit"),
        )
        assert preview.replacement_schedule is not None
        assert preview.replacement_schedule.event_zone == "UTC"
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None and record.local_date == date(2026, 10, 20)
        await _confirm(session, preview, owner_id, identity, billing, clock)
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None
        assert record.local_date == date(2026, 10, 22)
        assert record.local_time == time(13)
        assert record.version == 2 and record.reminder_preferences_version == 2
        reminders = (
            await session.scalars(
                select(ReminderRuleRecord).where(
                    ReminderRuleRecord.owner_id == owner_id,
                    ReminderRuleRecord.occurrence_id == occurrence_id,
                )
            )
        ).all()
        assert [(item.offset_kind, item.offset_amount, item.source) for item in reminders] == [
            ("elapsed_seconds", 3600, "explicit")
        ]
        assert billing.operations == [Operation.RESCHEDULE_PLAN, Operation.RESCHEDULE_PLAN]


@pytest.mark.asyncio
async def test_foreign_owner_and_expired_preview_cannot_write(mutation_sessions):
    at = datetime(2026, 10, 14, 9, tzinfo=UTC)
    owner_id = uuid4()
    other_id = uuid4()
    identity = _Identity(owner_id)
    billing = _Billing()
    clock = _TestClock(at)
    async with mutation_sessions() as session, session.begin():
        occurrence_id = await _seed_one_off(
            session, owner_id=owner_id, at=at, day=date(2026, 10, 20), with_reminder=False
        )
        with pytest.raises(MutationAccessDenied):
            await preview_mutation(
                MutationRequest(other_id, occurrence_id, 1, MutationAction.CANCEL),
                authenticated_owner_id=other_id,
                store=SqlAlchemyMutationStore(session),
                identity=_Identity(other_id),
                billing=billing,
                clock=clock,
            )
        stale_preview = await preview_mutation(
            MutationRequest(owner_id, occurrence_id, 1, MutationAction.CANCEL),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None
        record.version = 2
        await session.flush()
        with pytest.raises(MutationStalePreview):
            await _confirm(session, stale_preview, owner_id, identity, billing, clock)
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None and record.state == "open" and record.version == 2
        preview = await preview_mutation(
            MutationRequest(owner_id, occurrence_id, 2, MutationAction.CANCEL),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )
        _message, keyboard = await render_confirmable_mutation(
            session, preview, language="en", now=clock.now()
        )
        proof = mutation_proof_from_callback(
            keyboard.inline_keyboard[0][0].callback_data,
            preview.preview_id,
            preview.preview_version,
        )
        clock.at += timedelta(hours=25)
        with pytest.raises(MutationStalePreview):
            await confirm_mutation(
                proof,
                authenticated_owner_id=owner_id,
                store=SqlAlchemyMutationStore(session),
                identity=identity,
                billing=billing,
                confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
                clock=clock,
            )
        record = await session.get(OccurrenceRecord, occurrence_id)
        assert record is not None and record.state == "open" and record.version == 2


@pytest.mark.asyncio
async def test_this_and_future_reanchors_count_and_keeps_completed_history(
    mutation_sessions, monkeypatch
):
    at = datetime(2026, 5, 25, 9, tzinfo=UTC)
    owner_id = uuid4()
    identity = _Identity(owner_id)
    billing = _Billing()
    clock = _TestClock(at)
    draft = RecurringPlanDraft(
        kind=PlanKind.TASK,
        title="Review the release",
        start_date=date(2026, 5, 18),
        local_time=time(9),
        event_zone="UTC",
        recurrence=RecurrenceRule(
            RecurrenceFrequency.WEEKLY,
            interval=2,
            weekdays=(0,),
            count=4,
        ),
    )
    default_reminders = (ReminderOffset(OffsetKind.ELAPSED_SECONDS, 3600, "explicit"),)
    projection = project_recurrence(
        draft,
        owner_id=owner_id,
        default_zone="UTC",
        default_reminders=default_reminders,
        at=datetime(2026, 5, 1, 9, tzinfo=UTC),
        through=date(2026, 8, 1),
    )
    async with mutation_sessions() as session, session.begin():
        await SqlAlchemySeriesStore(session).persist_series(
            owner_id=owner_id,
            action_key=f"seed-{uuid4()}",
            payload_sha256="a" * 64,
            approved_preferences_version=1,
            draft=draft,
            projection=projection,
            effective_reminders=default_reminders,
            default_zone="UTC",
            quiet_zone="UTC",
            quiet_start=time(22),
            quiet_end=time(7),
            at=at,
            horizon_date=date(2026, 8, 1),
        )
        links = (
            await session.scalars(
                select(SeriesOccurrenceRecord)
                .where(SeriesOccurrenceRecord.owner_id == owner_id)
                .order_by(SeriesOccurrenceRecord.sequence)
            )
        ).all()
        assert [item.sequence for item in links] == [0, 1, 2, 3]
        target_id = links[1].occurrence_id
        first = await session.scalar(
            select(OccurrenceRecord).where(OccurrenceRecord.id == links[0].occurrence_id)
        )
        assert first is not None
        first.state = "completed"
        first.version += 1
        await session.flush()
        preview = await preview_mutation(
            MutationRequest(
                owner_id,
                target_id,
                1,
                MutationAction.RESCHEDULE,
                scope=MutationScope.THIS_AND_FUTURE,
                replacement=OneOffPlanDraft(
                    kind=draft.kind,
                    title=draft.title,
                    local_date=date(2026, 6, 8),
                    local_time=time(9),
                    event_zone="UTC",
                ),
            ),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )
        assert [item.local_date for item in preview.series_preview_schedules] == [
            date(2026, 6, 8),
            date(2026, 6, 22),
            date(2026, 7, 6),
        ]
        assert preview.series_projection is not None
        assert preview.series_projection.remaining_count == 3
        await _confirm(session, preview, owner_id, identity, billing, clock)
        refreshed_links = (
            await session.scalars(
                select(SeriesOccurrenceRecord)
                .where(SeriesOccurrenceRecord.owner_id == owner_id)
                .order_by(SeriesOccurrenceRecord.sequence)
            )
        ).all()
        refreshed = [
            await session.scalar(
                select(OccurrenceRecord).where(OccurrenceRecord.id == item.occurrence_id)
            )
            for item in refreshed_links
        ]
        assert refreshed[0] is not None and refreshed[0].state == "completed"
        assert refreshed[0].local_date == date(2026, 5, 18)
        assert [item.local_date for item in refreshed[1:] if item is not None] == [
            date(2026, 6, 8),
            date(2026, 6, 22),
            date(2026, 7, 6),
        ]
        assert refreshed_links[-1].sequence == 3

        cancel_preview = await preview_mutation(
            MutationRequest(
                owner_id,
                refreshed_links[1].occurrence_id,
                refreshed[1].version,
                MutationAction.CANCEL,
                scope=MutationScope.THIS_AND_FUTURE,
            ),
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            clock=clock,
        )
        assert cancel_preview.cancellation_projection is not None
        assert [item.sequence for item in cancel_preview.cancellation_projection.targets] == [
            1,
            2,
            3,
        ]
        message, keyboard = await render_confirmable_mutation(
            session, cancel_preview, language="en", now=clock.now()
        )
        assert "This and all future occurrences (3)" in message
        proof = mutation_proof_from_callback(
            keyboard.inline_keyboard[0][0].callback_data,
            cancel_preview.preview_id,
            cancel_preview.preview_version,
        )
        real_add_outbox_event = mutation_store_module.add_outbox_event
        terminal_events = 0

        async def fail_after_one_terminal_event(session, event, *, claim=None):
            nonlocal terminal_events
            if event.event_type == "planning.occurrence_cancelled":
                terminal_events += 1
                if terminal_events == 2:
                    raise RuntimeError("injected failure during future cancellation")
            await real_add_outbox_event(session, event, claim=claim)

        monkeypatch.setattr(
            mutation_store_module, "add_outbox_event", fail_after_one_terminal_event
        )
        with pytest.raises(RuntimeError, match="injected failure"):
            async with session.begin_nested():
                await confirm_mutation(
                    proof,
                    authenticated_owner_id=owner_id,
                    store=SqlAlchemyMutationStore(session),
                    identity=identity,
                    billing=billing,
                    confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
                    clock=clock,
                )
        rolled_back_rows = [
            await session.scalar(
                select(OccurrenceRecord).where(OccurrenceRecord.id == item.occurrence_id)
            )
            for item in refreshed_links
        ]
        assert [item.state for item in rolled_back_rows[1:] if item is not None] == [
            "open",
            "open",
            "open",
        ]
        series = await session.get(SeriesRecord, refreshed_links[0].series_id)
        assert series is not None and series.state == "active"
        monkeypatch.setattr(mutation_store_module, "add_outbox_event", real_add_outbox_event)
        cancel_receipt = await confirm_mutation(
            proof,
            authenticated_owner_id=owner_id,
            store=SqlAlchemyMutationStore(session),
            identity=identity,
            billing=billing,
            confirmation_verifier=SqlAlchemyTelegramMutationVerifier(session),
            clock=clock,
        )
        assert cancel_receipt["state"] == "cancelled"
        assert len(cancel_receipt["cancelled_occurrence_ids"]) == 3
        final_rows = [
            await session.scalar(
                select(OccurrenceRecord).where(OccurrenceRecord.id == item.occurrence_id)
            )
            for item in refreshed_links
        ]
        assert final_rows[0] is not None and final_rows[0].state == "completed"
        assert [item.state for item in final_rows[1:] if item is not None] == [
            "cancelled",
            "cancelled",
            "cancelled",
        ]
        series = await session.get(SeriesRecord, refreshed_links[0].series_id)
        assert series is not None and series.state == "stopped" and series.total_count == 4
        cancelled_events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.owner_id == owner_id,
                    OutboxEvent.event_type == "planning.occurrence_cancelled",
                )
            )
        ).all()
        assert {item.aggregate_id for item in cancelled_events} == {
            link.occurrence_id for link in refreshed_links[1:]
        }
        assert len(cancelled_events) == 3
        stopped_events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.owner_id == owner_id,
                    OutboxEvent.event_type == "planning.series_stopped",
                )
            )
        ).all()
        assert len(stopped_events) == 1
