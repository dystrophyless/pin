from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from pin.adapters.telegram.application_worker import process_pending_reminder_schedules
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.notifications.infrastructure.models import (
    NotificationReconciliationReceiptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_NOTIFICATIONS_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
NOW = datetime(2026, 10, 14, 7, tzinfo=UTC)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set PIN_TEST_NOTIFICATIONS_DATABASE_URL to the fresh owned issue-11 PostgreSQL database"
    ),
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def notification_sessions() -> Iterator[object]:
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.username) == ("127.0.0.1", 51017, "pin_test")
    assert url.database and url.database.startswith("pin_issue11_")
    sync_engine = create_sync_engine(url)
    try:
        with sync_engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            assert directory.rstrip("/").casefold() == DATA_DIRECTORY.casefold()
    finally:
        sync_engine.dispose()
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    engine = create_engine(DATABASE_URL)
    sessions = create_session_factory(engine)
    yield sessions
    run_local_async(engine.dispose)


def _event(
    *, owner_id, occurrence_id, event_type: str, version: int, at: datetime
) -> EventEnvelope:
    return EventEnvelope(
        id=uuid4(),
        event_type=event_type,
        schema_version=1,
        aggregate_id=occurrence_id,
        aggregate_version=version,
        owner_id=owner_id,
        occurred_at=at,
        payload={"test_fixture": "committed_planning_schedule"},
    )


def _outbox(event: EventEnvelope) -> OutboxEvent:
    return OutboxEvent(
        id=event.id,
        event_type=event.event_type,
        schema_version=event.schema_version,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        owner_id=event.owner_id,
        occurred_at=event.occurred_at,
        payload=event.to_dict(),
    )


def _preferences(owner_id, *, version: int = 1) -> PreferencesRecord:
    return PreferencesRecord(
        owner_id=owner_id,
        language="en",
        home_zone="Asia/Qyzylorda",
        reminder_calendar_days=1,
        reminder_elapsed_hours=1,
        quiet_start=time(22),
        quiet_end=time(8),
        review_time=time(20),
        version=version,
    )


def _occurrence(
    *, owner_id, plan_id, occurrence_id, version=1, approved_preferences_version=1, state="open"
) -> OccurrenceRecord:
    return OccurrenceRecord(
        id=occurrence_id,
        owner_id=owner_id,
        plan_item_id=plan_id,
        kind="appointment",
        event_zone="Asia/Qyzylorda",
        local_date=date(2026, 10, 16),
        local_time=time(14),
        starts_at=datetime(2026, 10, 16, 9, tzinfo=UTC),
        ends_at=datetime(2026, 10, 16, 10, tzinfo=UTC),
        duration_seconds=3600,
        state=state,
        version=version,
        reminder_preferences_version=approved_preferences_version,
    )


def _reminder(*, owner_id, occurrence_id, due_at: datetime) -> ReminderRuleRecord:
    return ReminderRuleRecord(
        id=uuid4(),
        owner_id=owner_id,
        occurrence_id=occurrence_id,
        offset_kind="calendar_days",
        offset_amount=1,
        source="default",
        due_at=due_at,
    )


def test_worker_reconciles_committed_schedule_events_and_replays_current_snapshot(
    notification_sessions,
) -> None:
    async def scenario() -> None:
        owner_id, plan_id, occurrence_id = uuid4(), uuid4(), uuid4()
        first_due = datetime(2026, 10, 15, 9, tzinfo=UTC)
        updated_due = datetime(2026, 10, 15, 10, tzinfo=UTC)
        first_event = _event(
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            event_type="planning.plan_created",
            version=1,
            at=NOW,
        )
        unsupported = EventEnvelope(
            id=uuid4(),
            event_type="planning.unrelated_event",
            schema_version=1,
            aggregate_id=uuid4(),
            aggregate_version=1,
            owner_id=owner_id,
            occurred_at=NOW,
            payload={},
        )
        async with notification_sessions() as session, session.begin():
            session.add(
                PreferencesRecord(
                    owner_id=owner_id,
                    language="en",
                    home_zone="Asia/Qyzylorda",
                    reminder_calendar_days=1,
                    reminder_elapsed_hours=1,
                    quiet_start=time(22),
                    quiet_end=time(8),
                    review_time=time(20),
                    version=1,
                )
            )
            session.add(
                PlanItemRecord(
                    id=plan_id,
                    owner_id=owner_id,
                    kind="appointment",
                    title="Appointment",
                    version=1,
                )
            )
            await session.flush()
            session.add(
                OccurrenceRecord(
                    id=occurrence_id,
                    owner_id=owner_id,
                    plan_item_id=plan_id,
                    kind="appointment",
                    event_zone="Asia/Qyzylorda",
                    local_date=date(2026, 10, 16),
                    local_time=time(14),
                    starts_at=datetime(2026, 10, 16, 9, tzinfo=UTC),
                    ends_at=datetime(2026, 10, 16, 10, tzinfo=UTC),
                    duration_seconds=3600,
                    state="open",
                    version=1,
                    reminder_preferences_version=1,
                )
            )
            session.add(
                ReminderRuleRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    occurrence_id=occurrence_id,
                    offset_kind="calendar_days",
                    offset_amount=1,
                    source="default",
                    due_at=first_due,
                )
            )
            await session.flush()
            session.add_all(
                [
                    OutboxEvent(
                        id=unsupported.id,
                        event_type=unsupported.event_type,
                        schema_version=unsupported.schema_version,
                        aggregate_id=unsupported.aggregate_id,
                        aggregate_version=unsupported.aggregate_version,
                        owner_id=unsupported.owner_id,
                        occurred_at=unsupported.occurred_at,
                        payload=unsupported.to_dict(),
                    ),
                    OutboxEvent(
                        id=first_event.id,
                        event_type=first_event.event_type,
                        schema_version=first_event.schema_version,
                        aggregate_id=first_event.aggregate_id,
                        aggregate_version=first_event.aggregate_version,
                        owner_id=first_event.owner_id,
                        occurred_at=first_event.occurred_at,
                        payload=first_event.to_dict(),
                        published_at=NOW,
                    ),
                ]
            )

        assert await process_pending_reminder_schedules(
            notification_sessions, now=NOW, limit=10
        ) == 1
        async with notification_sessions() as session:
            intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_id,
                    ReminderIntentRecord.occurrence_id == occurrence_id,
                )
            )
            receipt = await session.scalar(
                select(NotificationReconciliationReceiptRecord).where(
                    NotificationReconciliationReceiptRecord.owner_id == owner_id,
                    NotificationReconciliationReceiptRecord.event_id == first_event.id,
                )
            )
            assert intent is not None and receipt is not None
            assert (intent.state, intent.due_at, intent.source) == (
                "scheduled",
                first_due,
                "default",
            )
            assert intent.preferences_version == 1
            assert receipt.status == "applied"
            assert receipt.created_count == 1
            outbox_row = await session.get(OutboxEvent, first_event.id)
            assert outbox_row is not None and outbox_row.published_at == NOW

        # A new worker iteration after restart sees only unreceipted schedule events.
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 0

        replay = _event(
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            event_type="planning.plan_rescheduled",
            version=1,
            at=NOW + timedelta(minutes=1),
        )
        async with notification_sessions() as session, session.begin():
            current = await session.get(OccurrenceRecord, occurrence_id)
            rule = await session.scalar(
                select(ReminderRuleRecord).where(
                    ReminderRuleRecord.occurrence_id == occurrence_id
                )
            )
            intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_id,
                    ReminderIntentRecord.occurrence_id == occurrence_id,
                )
            )
            assert current is not None and rule is not None and intent is not None
            intent.not_before_at = NOW + timedelta(hours=2)
            current.version = 2
            current.starts_at = datetime(2026, 10, 16, 10, tzinfo=UTC)
            current.ends_at = datetime(2026, 10, 16, 11, tzinfo=UTC)
            rule.due_at = updated_due
            session.add(
                OutboxEvent(
                    id=replay.id,
                    event_type=replay.event_type,
                    schema_version=replay.schema_version,
                    aggregate_id=replay.aggregate_id,
                    aggregate_version=replay.aggregate_version,
                    owner_id=replay.owner_id,
                    occurred_at=replay.occurred_at,
                    payload=replay.to_dict(),
                )
            )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 1
        async with notification_sessions() as session:
            intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_id,
                    ReminderIntentRecord.occurrence_id == occurrence_id,
                )
            )
            assert intent is not None
            assert intent.due_at == updated_due
            assert intent.occurrence_version == 2
            assert intent.source_event_id == replay.id
            assert intent.dispatch_version == 2
            assert intent.not_before_at is None

        impossible_future = _event(
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            event_type="planning.plan_rescheduled",
            version=3,
            at=NOW + timedelta(minutes=2),
        )
        async with notification_sessions() as session, session.begin():
            session.add(
                OutboxEvent(
                    id=impossible_future.id,
                    event_type=impossible_future.event_type,
                    schema_version=impossible_future.schema_version,
                    aggregate_id=impossible_future.aggregate_id,
                    aggregate_version=impossible_future.aggregate_version,
                    owner_id=impossible_future.owner_id,
                    occurred_at=impossible_future.occurred_at,
                    payload=impossible_future.to_dict(),
                )
            )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 1
        async with notification_sessions() as session:
            intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_id,
                    ReminderIntentRecord.occurrence_id == occurrence_id,
                )
            )
            future_receipt = await session.scalar(
                select(NotificationReconciliationReceiptRecord).where(
                    NotificationReconciliationReceiptRecord.owner_id == owner_id,
                    NotificationReconciliationReceiptRecord.event_id == impossible_future.id,
                )
            )
            assert intent is not None and future_receipt is not None
            assert intent.due_at == updated_due
            assert future_receipt.status == "stale"

        # Stopping generation preserves reminders for materialized open rows.
        series_plan_id, series_id, series_occurrence_id = uuid4(), uuid4(), uuid4()
        series_due = datetime(2026, 10, 17, 9, tzinfo=UTC)
        series_created = _event(
            owner_id=owner_id,
            occurrence_id=series_id,
            event_type="planning.series_created",
            version=1,
            at=NOW + timedelta(minutes=3),
        )
        async with notification_sessions() as session, session.begin():
            session.add(
                PlanItemRecord(
                    id=series_plan_id,
                    owner_id=owner_id,
                    kind="appointment",
                    title="Recurring appointment",
                    version=1,
                )
            )
            await session.flush()
            session.add(
                SeriesRecord(
                    id=series_id,
                    owner_id=owner_id,
                    plan_item_id=series_plan_id,
                    version=1,
                    state="active",
                    anchor_date=date(2026, 10, 17),
                    anchor_sequence=0,
                    next_sequence=1,
                    total_count=3,
                    rule={"frequency": "daily", "interval": 1},
                    typed_draft={"title": "Recurring appointment"},
                )
            )
            session.add(
                OccurrenceRecord(
                    id=series_occurrence_id,
                    owner_id=owner_id,
                    plan_item_id=series_plan_id,
                    kind="appointment",
                    event_zone="Asia/Qyzylorda",
                    local_date=date(2026, 10, 17),
                    local_time=time(14),
                    starts_at=datetime(2026, 10, 17, 9, tzinfo=UTC),
                    ends_at=datetime(2026, 10, 17, 10, tzinfo=UTC),
                    duration_seconds=3600,
                    state="open",
                    version=1,
                    reminder_preferences_version=1,
                )
            )
            await session.flush()
            session.add(
                SeriesOccurrenceRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    series_id=series_id,
                    occurrence_id=series_occurrence_id,
                    sequence=0,
                    recurrence_key="2026-10-17",
                )
            )
            session.add(
                ReminderRuleRecord(
                    id=uuid4(),
                    owner_id=owner_id,
                    occurrence_id=series_occurrence_id,
                    offset_kind="calendar_days",
                    offset_amount=1,
                    source="default",
                    due_at=series_due,
                )
            )
            session.add(
                OutboxEvent(
                    id=series_created.id,
                    event_type=series_created.event_type,
                    schema_version=series_created.schema_version,
                    aggregate_id=series_created.aggregate_id,
                    aggregate_version=series_created.aggregate_version,
                    owner_id=series_created.owner_id,
                    occurred_at=series_created.occurred_at,
                    payload=series_created.to_dict(),
                )
            )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 1

        series_stopped = _event(
            owner_id=owner_id,
            occurrence_id=series_id,
            event_type="planning.series_stopped",
            version=2,
            at=NOW + timedelta(minutes=4),
        )
        async with notification_sessions() as session, session.begin():
            series = await session.get(SeriesRecord, series_id)
            assert series is not None
            series.version = 2
            series.state = "stopped"
            session.add(
                OutboxEvent(
                    id=series_stopped.id,
                    event_type=series_stopped.event_type,
                    schema_version=series_stopped.schema_version,
                    aggregate_id=series_stopped.aggregate_id,
                    aggregate_version=series_stopped.aggregate_version,
                    owner_id=series_stopped.owner_id,
                    occurred_at=series_stopped.occurred_at,
                    payload=series_stopped.to_dict(),
                )
            )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 1
        async with notification_sessions() as session:
            series_intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_id,
                    ReminderIntentRecord.occurrence_id == series_occurrence_id,
                )
            )
            assert series_intent is not None
            assert series_intent.state == "scheduled"
            assert series_intent.due_at == series_due
            assert series_intent.series_version == 2
            assert series_intent.dispatch_version == 2

    run_local_async(scenario)


def test_reconciliation_invalidates_terminal_legacy_and_preference_stale_intents(
    notification_sessions,
) -> None:
    async def scenario() -> None:
        owner_id, plan_id = uuid4(), uuid4()
        cases = (
            ("completed", "completed", 2),
            ("cancelled", "cancelled", 2),
            ("preferences_changed", "open", 1),
            ("legacy", "open", None),
        )
        identifiers = {name: uuid4() for name, _, _ in cases}
        due = datetime(2026, 10, 15, 9, tzinfo=UTC)
        events = {
            name: _event(
                owner_id=owner_id,
                occurrence_id=occurrence_id,
                event_type="planning.plan_rescheduled",
                version=2,
                at=NOW,
            )
            for name, occurrence_id in identifiers.items()
        }
        async with notification_sessions() as session, session.begin():
            session.add(_preferences(owner_id, version=2))
            session.add(
                PlanItemRecord(
                    id=plan_id,
                    owner_id=owner_id,
                    kind="appointment",
                    title="Eligibility cases",
                    version=1,
                )
            )
            await session.flush()
            for name, state, approved_version in cases:
                occurrence_id = identifiers[name]
                session.add(
                    _occurrence(
                        owner_id=owner_id,
                        plan_id=plan_id,
                        occurrence_id=occurrence_id,
                        version=2,
                        approved_preferences_version=approved_version,
                        state=state,
                    )
                )
                session.add(_reminder(owner_id=owner_id, occurrence_id=occurrence_id, due_at=due))
                session.add(_outbox(events[name]))
            await session.flush()
            for name, _, _ in cases:
                occurrence_id = identifiers[name]
                session.add(
                    ReminderIntentRecord(
                        id=uuid4(),
                        owner_id=owner_id,
                        plan_item_id=plan_id,
                        occurrence_id=occurrence_id,
                        series_id=None,
                        plan_version=1,
                        occurrence_version=1,
                        preferences_version=1,
                        series_version=None,
                        offset_kind="calendar_days",
                        offset_amount=1,
                        source="default",
                        due_at=due,
                        state="scheduled",
                        dispatch_version=1,
                        source_event_id=uuid4(),
                    )
                )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 4
        async with notification_sessions() as session:
            intents = list(
                (
                    await session.scalars(
                        select(ReminderIntentRecord).where(
                            ReminderIntentRecord.owner_id == owner_id,
                            ReminderIntentRecord.plan_item_id == plan_id,
                        )
                    )
                ).all()
            )
            assert len(intents) == 4
            assert all(row.state == "invalidated" for row in intents)
            assert all(row.dispatch_version == 2 for row in intents)
            assert all(row.invalidated_at is not None for row in intents)

    run_local_async(scenario)


def test_wrong_owner_event_is_stale_and_concurrent_workers_create_one_intent(
    notification_sessions,
) -> None:
    async def scenario() -> None:
        owner_a, owner_b = uuid4(), uuid4()
        plan_a, occurrence_a = uuid4(), uuid4()
        plan_b, occurrence_b = uuid4(), uuid4()
        due = datetime(2026, 10, 15, 9, tzinfo=UTC)
        wrong_owner = _event(
            owner_id=owner_a,
            occurrence_id=occurrence_b,
            event_type="planning.plan_created",
            version=1,
            at=NOW,
        )
        valid = _event(
            owner_id=owner_a,
            occurrence_id=occurrence_a,
            event_type="planning.plan_created",
            version=1,
            at=NOW,
        )
        async with notification_sessions() as session, session.begin():
            session.add_all([_preferences(owner_a), _preferences(owner_b)])
            session.add_all(
                [
                    PlanItemRecord(
                        id=plan_a,
                        owner_id=owner_a,
                        kind="appointment",
                        title="Owner A",
                        version=1,
                    ),
                    PlanItemRecord(
                        id=plan_b,
                        owner_id=owner_b,
                        kind="appointment",
                        title="Owner B",
                        version=1,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    _occurrence(
                        owner_id=owner_a,
                        plan_id=plan_a,
                        occurrence_id=occurrence_a,
                    ),
                    _occurrence(
                        owner_id=owner_b,
                        plan_id=plan_b,
                        occurrence_id=occurrence_b,
                    ),
                    _reminder(owner_id=owner_a, occurrence_id=occurrence_a, due_at=due),
                    _reminder(owner_id=owner_b, occurrence_id=occurrence_b, due_at=due),
                    _outbox(wrong_owner),
                ]
            )
            await session.flush()
            session.add(
                ReminderIntentRecord(
                    id=uuid4(),
                    owner_id=owner_b,
                    plan_item_id=plan_b,
                    occurrence_id=occurrence_b,
                    series_id=None,
                    plan_version=1,
                    occurrence_version=1,
                    preferences_version=1,
                    series_version=None,
                    offset_kind="calendar_days",
                    offset_amount=1,
                    source="default",
                    due_at=due,
                    state="scheduled",
                    dispatch_version=1,
                    source_event_id=uuid4(),
                )
            )
        assert await process_pending_reminder_schedules(notification_sessions, now=NOW) == 1
        async with notification_sessions() as session, session.begin():
            session.add(_outbox(valid))
        outcomes = await asyncio.gather(
            process_pending_reminder_schedules(notification_sessions, now=NOW, limit=1),
            process_pending_reminder_schedules(notification_sessions, now=NOW, limit=1),
        )
        assert sorted(outcomes) == [0, 1]
        async with notification_sessions() as session:
            owner_b_intent = await session.scalar(
                select(ReminderIntentRecord).where(
                    ReminderIntentRecord.owner_id == owner_b,
                    ReminderIntentRecord.occurrence_id == occurrence_b,
                )
            )
            wrong_receipt = await session.scalar(
                select(NotificationReconciliationReceiptRecord).where(
                    NotificationReconciliationReceiptRecord.owner_id == owner_a,
                    NotificationReconciliationReceiptRecord.event_id == wrong_owner.id,
                )
            )
            owner_a_intents = list(
                (
                    await session.scalars(
                        select(ReminderIntentRecord).where(
                            ReminderIntentRecord.owner_id == owner_a,
                            ReminderIntentRecord.occurrence_id == occurrence_a,
                        )
                    )
                ).all()
            )
            assert owner_b_intent is not None
            assert owner_b_intent.state == "scheduled"
            assert owner_b_intent.dispatch_version == 1
            assert wrong_receipt is not None and wrong_receipt.status == "stale"
            assert len(owner_a_intents) == 1

    run_local_async(scenario)
