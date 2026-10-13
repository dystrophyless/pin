from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.adapters.telegram.presentation.agenda import render_agenda
from pin.adapters.telegram.presentation.callbacks import CallbackTokens, InvalidCallbackToken
from pin.adapters.telegram.presentation.tokens import (
    SqlAlchemyCallbackTokenStore,
    TelegramCallbackTokenRecord,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    UsageReservationRecord,
)
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.planning.application.agenda import (
    AgendaView,
    open_agenda,
    read_agenda_page,
    remember_page_start,
)
from pin.contexts.planning.infrastructure.agenda import (
    SqlAlchemyAgendaReadStore,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.persistence.models import ActionReceipt
from pin.platform.jobs import JobLane, receive_update
from pin.platform.models import Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_AGENDA_DATABASE_URL")
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_AGENDA_DATABASE_URL to the owned issue-9 PostgreSQL database",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def agenda_database() -> Iterator[str]:
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.username) == ("127.0.0.1", 51017, "pin_test")
    assert url.database and url.database.startswith("pin_issue9_")
    sync_engine = create_sync_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with sync_engine.connect() as connection:
            directory = str(connection.execute(text("SHOW data_directory")).scalar_one()).replace(
                "\\", "/"
            )
            assert directory.rstrip("/").casefold() == DATA_DIRECTORY.casefold()
    finally:
        sync_engine.dispose()
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    yield DATABASE_URL


async def _add_occurrence(
    session: AsyncSession,
    owner_id: UUID,
    *,
    name: str,
    kind: str,
    event_zone: str,
    local_date: date,
    local_time: time | None,
    starts_at: datetime | None,
    state: str = "open",
    ends_at: datetime | None = None,
    deadline_at: datetime | None = None,
    end_local_date: date | None = None,
    end_local_time: time | None = None,
) -> UUID:
    item_id, occurrence_id = uuid4(), uuid4()
    session.add(PlanItemRecord(id=item_id, owner_id=owner_id, kind=kind, title=name, version=1))
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=item_id,
            kind=kind,
            event_zone=event_zone,
            local_date=local_date,
            local_time=local_time,
            fold=None,
            end_local_date=end_local_date,
            end_local_time=end_local_time,
            end_fold=None,
            starts_at=starts_at,
            ends_at=ends_at,
            deadline_at=deadline_at,
            duration_seconds=3600 if kind == "appointment" else None,
            state=state,
            version=1,
        )
    )
    await session.flush()
    return occurrence_id


async def _queue_worker_update(
    session: AsyncSession,
    *,
    owner_id: UUID,
    bot_id: int,
    telegram_user_id: int,
    update_id: int,
    now: datetime,
    text_value: str | None = None,
    callback_data: str | None = None,
) -> None:
    if (text_value is None) == (callback_data is None):
        raise ValueError("provide exactly one private message or callback")
    if text_value is not None:
        payload = {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 1,
                "date": int(now.timestamp()),
                "chat": {"id": telegram_user_id, "type": "private"},
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Agenda test"},
                "text": text_value,
            },
        }
        update_type = "message"
    else:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Agenda test"},
                "message": {
                    "message_id": update_id + 1,
                    "chat": {"id": telegram_user_id, "type": "private"},
                },
                "data": callback_data,
            },
        }
        update_type = "callback_query"
    await receive_update(
        session,
        bot_id=bot_id,
        update_id=update_id,
        owner_id=owner_id,
        update_type=update_type,
        payload=payload,
        lane=JobLane.APPLICATION,
        conversation_ordered=False,
    )


async def _process_worker_update(sessions, *, now: datetime, worker_id: str) -> tuple[UUID, UUID]:
    claim = await claim_application_job(sessions, worker_id=worker_id)
    assert claim is not None
    await process_application_job(sessions, claim, now=now)
    assert claim.owner_id is not None
    return claim.owner_id, claim.job_id


async def _worker_reply(session: AsyncSession, job_id: UUID) -> dict[str, object]:
    event = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.causation_id == job_id,
            OutboxEvent.event_type == "telegram.reply.requested",
        )
    )
    assert event is not None
    payload = event.payload["payload"]
    assert isinstance(payload, dict)
    return payload


def test_postgres_agenda_owner_boundaries_status_and_event_zones(agenda_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        owner_a, owner_b = uuid4(), uuid4()
        now = datetime(2026, 6, 2, 12, tzinfo=UTC)
        ids: dict[str, UUID] = {}
        try:
            async with sessions() as session, session.begin():
                ids["start"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Start boundary",
                    kind="task",
                    event_zone="Asia/Almaty",
                    local_date=date(2026, 6, 2),
                    local_time=time(0),
                    starts_at=datetime(2026, 6, 1, 19, tzinfo=UTC),
                )
                ids["end"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="End boundary",
                    kind="task",
                    event_zone="Asia/Almaty",
                    local_date=date(2026, 6, 3),
                    local_time=time(0),
                    starts_at=datetime(2026, 6, 2, 19, tzinfo=UTC),
                )
                ids["zone_crossing"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Zone crossing",
                    kind="task",
                    event_zone="America/New_York",
                    local_date=date(2026, 6, 1),
                    local_time=time(20, 30),
                    starts_at=datetime(2026, 6, 2, 0, 30, tzinfo=UTC),
                )
                ids["overdue"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Overdue timed",
                    kind="task",
                    event_zone="UTC",
                    local_date=date(2026, 6, 1),
                    local_time=time(18),
                    starts_at=datetime(2026, 6, 1, 18, tzinfo=UTC),
                )
                ids["ongoing_appointment"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Ongoing appointment",
                    kind="appointment",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=time(11),
                    starts_at=datetime(2026, 6, 2, 11, tzinfo=UTC),
                    ends_at=datetime(2026, 6, 2, 13, tzinfo=UTC),
                    end_local_date=date(2026, 6, 2),
                    end_local_time=time(13),
                )
                ids["just_ended_appointment"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Just ended appointment",
                    kind="appointment",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=time(10),
                    starts_at=datetime(2026, 6, 2, 10, tzinfo=UTC),
                    ends_at=datetime(2026, 6, 2, 11, tzinfo=UTC),
                    end_local_date=date(2026, 6, 2),
                    end_local_time=time(11),
                )
                ids["overdue_deadline"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Overdue deadline",
                    kind="deadline",
                    event_zone="UTC",
                    local_date=date(2026, 6, 1),
                    local_time=time(18),
                    starts_at=None,
                    deadline_at=datetime(2026, 6, 1, 18, tzinfo=UTC),
                )
                ids["older_untimed"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Older untimed",
                    kind="untimed",
                    event_zone="UTC",
                    local_date=date(2026, 5, 1),
                    local_time=None,
                    starts_at=None,
                )
                ids["today_untimed"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Today untimed",
                    kind="untimed",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=None,
                    starts_at=None,
                )
                ids["cancelled"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Cancelled",
                    kind="task",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=time(10),
                    starts_at=datetime(2026, 6, 2, 10, tzinfo=UTC),
                    state="cancelled",
                )
                ids["completed"] = await _add_occurrence(
                    session,
                    owner_a,
                    name="Completed",
                    kind="task",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=time(11),
                    starts_at=datetime(2026, 6, 2, 11, tzinfo=UTC),
                    state="completed",
                )
                ids["other_owner"] = await _add_occurrence(
                    session,
                    owner_b,
                    name="Other owner",
                    kind="task",
                    event_zone="UTC",
                    local_date=date(2026, 6, 2),
                    local_time=time(10),
                    starts_at=datetime(2026, 6, 2, 10, tzinfo=UTC),
                )
                page = await open_agenda(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=owner_a,
                    view=AgendaView.TODAY,
                    display_zone="Asia/Almaty",
                    now=now,
                )
                assert set(page.snapshot.item_ids) == {
                    str(ids[name])
                    for name in (
                        "start",
                        "zone_crossing",
                        "overdue",
                        "ongoing_appointment",
                        "just_ended_appointment",
                        "overdue_deadline",
                        "older_untimed",
                        "today_untimed",
                    )
                }
                rendered = render_agenda(page, language="en")
                sections = {entry.record.title: entry.section.value for entry in page.entries}
                assert sections["Ongoing appointment"] == "scheduled"
                assert sections["Just ended appointment"] == "overdue"
                assert sections["Overdue timed"] == "overdue"
                assert sections["Overdue deadline"] == "overdue"
                assert sections["Older untimed"] == "overdue"
                assert "America/New\\_York" in rendered.markdown
                older_line = next(
                    line for line in rendered.plain_text.splitlines() if "Older untimed" in line
                )
                assert "00:00" not in older_line

                completed = await open_agenda(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=owner_a,
                    view=AgendaView.TODAY,
                    display_zone="Asia/Almaty",
                    include_completed=True,
                    now=now,
                )
                assert str(ids["completed"]) in completed.snapshot.item_ids
                assert str(ids["cancelled"]) not in completed.snapshot.item_ids
            await engine.dispose()
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_agenda_maps_exact_dst_week_edges(agenda_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        spring_owner, autumn_owner = uuid4(), uuid4()
        try:
            async with sessions() as session, session.begin():
                spring_start = await _add_occurrence(
                    session,
                    spring_owner,
                    name="Spring Monday start",
                    kind="task",
                    event_zone="Europe/Berlin",
                    local_date=date(2026, 3, 23),
                    local_time=time(0),
                    starts_at=datetime(2026, 3, 22, 23, tzinfo=UTC),
                )
                spring_end = await _add_occurrence(
                    session,
                    spring_owner,
                    name="Spring Monday end",
                    kind="task",
                    event_zone="Europe/Berlin",
                    local_date=date(2026, 3, 30),
                    local_time=time(0),
                    starts_at=datetime(2026, 3, 29, 22, tzinfo=UTC),
                )
                autumn_start = await _add_occurrence(
                    session,
                    autumn_owner,
                    name="Autumn Monday start",
                    kind="task",
                    event_zone="Europe/Berlin",
                    local_date=date(2026, 10, 19),
                    local_time=time(0),
                    starts_at=datetime(2026, 10, 18, 22, tzinfo=UTC),
                )
                autumn_end = await _add_occurrence(
                    session,
                    autumn_owner,
                    name="Autumn Monday end",
                    kind="task",
                    event_zone="Europe/Berlin",
                    local_date=date(2026, 10, 26),
                    local_time=time(0),
                    starts_at=datetime(2026, 10, 25, 23, tzinfo=UTC),
                )
                spring = await open_agenda(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=spring_owner,
                    view=AgendaView.WEEK,
                    display_zone="Europe/Berlin",
                    now=datetime(2026, 3, 29, 12, tzinfo=UTC),
                )
                assert (
                    spring.snapshot.window.ends_at - spring.snapshot.window.starts_at
                    == timedelta(hours=167)
                )
                assert spring.snapshot.item_ids == (str(spring_start),)
                autumn = await open_agenda(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=autumn_owner,
                    view=AgendaView.WEEK,
                    display_zone="Europe/Berlin",
                    now=datetime(2026, 10, 25, 12, tzinfo=UTC),
                )
                assert (
                    autumn.snapshot.window.ends_at - autumn.snapshot.window.starts_at
                    == timedelta(hours=169)
                )
                assert autumn.snapshot.item_ids == (str(autumn_start),)
                assert str(spring_end) not in spring.snapshot.item_ids
                assert str(autumn_end) not in autumn.snapshot.item_ids
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_snapshot_paging_owner_fence_updates_and_expiry(agenda_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        owner, other_owner = uuid4(), uuid4()
        now = datetime(2026, 6, 2, 8, tzinfo=UTC)
        occurrence_ids: list[UUID] = []
        try:
            async with sessions() as session, session.begin():
                for index in range(8):
                    occurrence_ids.append(
                        await _add_occurrence(
                            session,
                            owner,
                            name=f"Plan {index} " + "_" * 440,
                            kind="task",
                            event_zone="UTC",
                            local_date=date(2026, 6, 2),
                            local_time=time(9 + index),
                            starts_at=datetime(2026, 6, 2, 9 + index, tzinfo=UTC),
                        )
                    )
                first = await open_agenda(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=owner,
                    view=AgendaView.TODAY,
                    display_zone="UTC",
                    now=now,
                )
                reply = render_agenda(first, language="ru")
                next_button = next(
                    button for button in reply.buttons if button.action == "agenda_page"
                )
                snapshot_text, offset_text = next_button.value.split(":")
                snapshot_id, offset = UUID(snapshot_text), int(offset_text)
                token = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).issue(
                    owner_id=owner,
                    action=next_button.action,
                    target_id=next_button.value,
                    target_version=1,
                    expires_at=now + timedelta(minutes=10),
                    now=now,
                )

            async with sessions() as session, session.begin():
                with pytest.raises(InvalidCallbackToken):
                    await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).consume(
                        token, actor_id=other_owner, current_target_version=1, now=now
                    )
            async with sessions() as session, session.begin():
                binding = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).consume(
                    token, actor_id=owner, current_target_version=1, now=now
                )
                assert binding.target_id == next_button.value
                await remember_page_start(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=owner,
                    snapshot_id=snapshot_id,
                    start_index=offset,
                )
                await session.execute(
                    update(PlanItemRecord)
                    .where(
                        PlanItemRecord.id
                        == (
                            await session.scalar(
                                select(OccurrenceRecord.plan_item_id).where(
                                    OccurrenceRecord.id == occurrence_ids[-1]
                                )
                            )
                        )
                    )
                    .values(title="Updated while paginating")
                )

            async with sessions() as session:
                resumed = await read_agenda_page(
                    SqlAlchemyAgendaReadStore(session),
                    owner_id=owner,
                    snapshot_id=snapshot_id,
                    start_index=offset,
                    now=now + timedelta(minutes=1),
                )
                assert resumed is not None
                assert resumed.previous_index == 0
                assert tuple(entry.occurrence_id for entry in resumed.entries) == tuple(
                    occurrence_ids[offset:]
                )
                assert resumed.entries[-1].record.title == "Updated while paginating"
                assert (
                    await SqlAlchemyAgendaReadStore(session).get_snapshot(
                        other_owner, snapshot_id, now=now
                    )
                    is None
                )
                assert (
                    await SqlAlchemyAgendaReadStore(session).get_snapshot(
                        owner, snapshot_id, now=now + timedelta(hours=25)
                    )
                    is None
                )
                assert (
                    await read_agenda_page(
                        SqlAlchemyAgendaReadStore(session),
                        owner_id=owner,
                        snapshot_id=snapshot_id,
                        start_index=offset,
                        now=now + timedelta(hours=25),
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_application_worker_commits_agenda_reply_and_receipt_together(
    agenda_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        owner, telegram_user_id = uuid4(), 930_000_000 + uuid4().int % 50_000_000
        bot_id, update_id = 900_000_000 + uuid4().int % 100_000_000, uuid4().int % 1_000_000_000
        now = datetime.now(UTC)
        local_date = now.astimezone(UTC).date()
        try:
            async with sessions() as session, session.begin():
                session.add(
                    TelegramOwnerRecord(
                        bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner
                    )
                )
                session.add(
                    PreferencesRecord(
                        owner_id=owner,
                        language="en",
                        home_zone="UTC",
                        reminder_calendar_days=1,
                        reminder_elapsed_hours=1,
                        quiet_start=time(22),
                        quiet_end=time(8),
                        review_time=time(20),
                        version=1,
                    )
                )
                session.add(AccessAccountRecord(owner_id=owner))
                await _add_occurrence(
                    session,
                    owner,
                    name="Worker agenda item",
                    kind="task",
                    event_zone="UTC",
                    local_date=local_date,
                    local_time=time(12),
                    starts_at=datetime.combine(local_date, time(12), UTC),
                )
                await receive_update(
                    session,
                    bot_id=bot_id,
                    update_id=update_id,
                    owner_id=owner,
                    update_type="message",
                    payload={
                        "update_id": update_id,
                        "message": {
                            "message_id": update_id + 1,
                            "date": int(now.timestamp()),
                            "chat": {"id": telegram_user_id, "type": "private"},
                            "from": {
                                "id": telegram_user_id,
                                "is_bot": False,
                                "first_name": "Agenda test",
                            },
                            "text": "/today",
                        },
                    },
                    lane=JobLane.APPLICATION,
                    conversation_ordered=False,
                )

            claim = await claim_application_job(sessions, worker_id="agenda-test-worker")
            assert claim is not None
            await process_application_job(sessions, claim, now=now)
            async with sessions() as session:
                job = await session.get(Job, claim.job_id)
                assert job is not None and job.status == "succeeded"
                receipt = await session.scalar(
                    select(ActionReceipt).where(
                        ActionReceipt.owner_id == owner,
                        ActionReceipt.action_key == f"job:{claim.job_id}:private_text_applied",
                    )
                )
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.causation_id == claim.job_id,
                        OutboxEvent.event_type == "telegram.reply.requested",
                    )
                )
                assert receipt is not None and receipt.result == {"status": "processed"}
                assert event is not None
                reply_payload = event.payload["payload"]
                assert "Worker agenda item" in reply_payload["plain_text"]
                callback_data = reply_payload["reply_markup"]["inline_keyboard"][0][0][
                    "callback_data"
                ]
                assert len(callback_data.encode("utf-8")) <= 64
                token_record = await session.get(
                    TelegramCallbackTokenRecord,
                    hashlib.sha256(callback_data.encode("ascii")).hexdigest(),
                )
                assert token_record is not None and token_record.owner_id == owner
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_worker_enforces_view_access_for_commands_and_callbacks(
    agenda_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        expired_owner, deleted_owner = uuid4(), uuid4()
        owners = (expired_owner, deleted_owner)
        telegram_ids = {owner: 940_000_000 + owner.int % 40_000_000 for owner in owners}
        bot_id = 910_000_000 + uuid4().int % 80_000_000
        now = datetime.now(UTC)
        local_day = now.date()
        update_ids = [uuid4().int % 1_000_000_000 for _ in range(4)]
        jobs: list[tuple[UUID, UUID]] = []
        try:
            async with sessions() as session, session.begin():
                for owner, language in ((expired_owner, "en"), (deleted_owner, "ru")):
                    telegram_user_id = telegram_ids[owner]
                    session.add(
                        TelegramOwnerRecord(
                            bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner
                        )
                    )
                    session.add(
                        PreferencesRecord(
                            owner_id=owner,
                            language=language,
                            home_zone="UTC",
                            reminder_calendar_days=1,
                            reminder_elapsed_hours=1,
                            quiet_start=time(22),
                            quiet_end=time(8),
                            review_time=time(20),
                            version=1,
                        )
                    )
                    session.add(
                        AccessAccountRecord(
                            owner_id=owner,
                            deleted_at=now - timedelta(days=1) if owner == deleted_owner else None,
                        )
                    )
                    if owner == expired_owner:
                        session.add(
                            AccessPeriodRecord(
                                owner_id=owner,
                                period_id=f"expired-{owner}",
                                policy_kind="trial",
                                policy_version="v1",
                                starts_at=now - timedelta(days=8),
                                ends_at=now - timedelta(days=1),
                                ai_turn_limit=250,
                                voice_second_limit=900,
                            )
                        )
                    await _add_occurrence(
                        session,
                        owner,
                        name=(
                            "Expired command item"
                            if owner == expired_owner
                            else "Deleted command secret"
                        ),
                        kind="task",
                        event_zone="UTC",
                        local_date=local_day,
                        local_time=time(12),
                        starts_at=datetime.combine(local_day, time(12), UTC),
                    )
                    await _add_occurrence(
                        session,
                        owner,
                        name=(
                            "Expired completed item"
                            if owner == expired_owner
                            else "Deleted completed secret"
                        ),
                        kind="task",
                        state="completed",
                        event_zone="UTC",
                        local_date=local_day,
                        local_time=time(13),
                        starts_at=datetime.combine(local_day, time(13), UTC),
                    )
                    await _queue_worker_update(
                        session,
                        owner_id=owner,
                        bot_id=bot_id,
                        telegram_user_id=telegram_user_id,
                        update_id=update_ids[0 if owner == expired_owner else 1],
                        now=now,
                        text_value="/today",
                    )
                    token = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).issue(
                        owner_id=owner,
                        action="agenda_completed",
                        target_id="today|1",
                        target_version=1,
                        expires_at=now + timedelta(minutes=10),
                        now=now,
                    )
                    await _queue_worker_update(
                        session,
                        owner_id=owner,
                        bot_id=bot_id,
                        telegram_user_id=telegram_user_id,
                        update_id=update_ids[2 if owner == expired_owner else 3],
                        now=now,
                        callback_data=token,
                    )

            for index in range(4):
                jobs.append(
                    await _process_worker_update(
                        sessions, now=now, worker_id=f"agenda-access-{index}"
                    )
                )

            async with sessions() as session:
                replies = [
                    (owner_id, await _worker_reply(session, job_id)) for owner_id, job_id in jobs
                ]
                expired_replies = [
                    reply for owner_id, reply in replies if owner_id == expired_owner
                ]
                deleted_replies = [
                    reply for owner_id, reply in replies if owner_id == deleted_owner
                ]
                expired_command = next(
                    reply
                    for reply in expired_replies
                    if "Expired command item" in reply["plain_text"]
                )
                expired_callback = next(
                    reply
                    for reply in expired_replies
                    if "Expired completed item" in reply["plain_text"]
                )
                assert "Expired command item" in expired_command["plain_text"]
                assert "Expired completed item" in expired_callback["plain_text"]
                assert len(deleted_replies) == 2
                for reply in deleted_replies:
                    assert "Deleted " not in reply["plain_text"]
                    assert "недоступны" in reply["plain_text"]
                    assert "reply_markup" not in reply
                usage_reservations = await session.scalars(
                    select(UsageReservationRecord).where(
                        UsageReservationRecord.owner_id.in_(owners)
                    )
                )
                assert usage_reservations.all() == []
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_worker_agenda_callbacks_survive_settings_draft_and_localize_recovery(
    agenda_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(agenda_database)
        sessions = create_session_factory(engine)
        draft_owner, changed_owner = uuid4(), uuid4()
        owners = (draft_owner, changed_owner)
        telegram_ids = {owner: 950_000_000 + owner.int % 30_000_000 for owner in owners}
        bot_id = 920_000_000 + uuid4().int % 70_000_000
        now = datetime.now(UTC)
        local_day = now.date()
        update_ids = [uuid4().int % 1_000_000_000 for _ in range(3)]
        jobs: list[tuple[UUID, UUID]] = []
        try:
            async with sessions() as session, session.begin():
                for owner, language in ((draft_owner, "en"), (changed_owner, "ru")):
                    telegram_user_id = telegram_ids[owner]
                    session.add(
                        TelegramOwnerRecord(
                            bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner
                        )
                    )
                    session.add(
                        PreferencesRecord(
                            owner_id=owner,
                            language=language,
                            home_zone="UTC",
                            reminder_calendar_days=1,
                            reminder_elapsed_hours=1,
                            quiet_start=time(22),
                            quiet_end=time(8),
                            review_time=time(20),
                            version=1,
                        )
                    )
                    session.add(AccessAccountRecord(owner_id=owner))
                    await _add_occurrence(
                        session,
                        owner,
                        name=(
                            "Draft agenda item" if owner == draft_owner else "Stale agenda secret"
                        ),
                        kind="task",
                        event_zone="UTC",
                        local_date=local_day,
                        local_time=time(12),
                        starts_at=datetime.combine(local_day, time(12), UTC),
                    )
                    page = await open_agenda(
                        SqlAlchemyAgendaReadStore(session),
                        owner_id=owner,
                        view=AgendaView.TODAY,
                        display_zone="UTC",
                        now=now,
                    )
                    token = await CallbackTokens(SqlAlchemyCallbackTokenStore(session)).issue(
                        owner_id=owner,
                        action="agenda_page",
                        target_id=f"{page.snapshot.snapshot_id}:0",
                        target_version=1,
                        expires_at=now + timedelta(minutes=10),
                        now=now,
                    )
                    if owner == draft_owner:
                        session.add(
                            OnboardingRecord(
                                owner_id=owner,
                                draft_id=uuid4(),
                                mode="preferences",
                                step="quiet_review",
                                draft={"language": "en", "home_zone": "UTC"},
                                base_profile_version=1,
                                version=2,
                            )
                        )
                    else:
                        await session.execute(
                            update(PreferencesRecord)
                            .where(PreferencesRecord.owner_id == owner)
                            .values(version=2)
                        )
                    await _queue_worker_update(
                        session,
                        owner_id=owner,
                        bot_id=bot_id,
                        telegram_user_id=telegram_user_id,
                        update_id=update_ids[0 if owner == draft_owner else 1],
                        now=now,
                        callback_data=token,
                    )
                await _queue_worker_update(
                    session,
                    owner_id=draft_owner,
                    bot_id=bot_id,
                    telegram_user_id=telegram_ids[draft_owner],
                    update_id=update_ids[2],
                    now=now,
                    callback_data="untrusted-token-content",
                )

            for index in range(3):
                jobs.append(
                    await _process_worker_update(
                        sessions, now=now, worker_id=f"agenda-version-{index}"
                    )
                )

            async with sessions() as session:
                replies = [
                    (owner_id, await _worker_reply(session, job_id)) for owner_id, job_id in jobs
                ]
                draft_replies = [reply for owner_id, reply in replies if owner_id == draft_owner]
                changed_version_reply = next(
                    reply for owner_id, reply in replies if owner_id == changed_owner
                )
                valid_draft_reply = next(
                    reply for reply in draft_replies if "Draft agenda item" in reply["plain_text"]
                )
                invalid_draft_reply = next(
                    reply for reply in draft_replies if "/today" in reply["plain_text"]
                )
                assert "Draft agenda item" in valid_draft_reply["plain_text"]
                assert "Stale agenda secret" not in changed_version_reply["plain_text"]
                assert "Отправьте /start" in changed_version_reply["plain_text"]
                assert "/today" in changed_version_reply["plain_text"]
                assert "/week" in changed_version_reply["plain_text"]
                assert "Draft agenda item" not in invalid_draft_reply["plain_text"]
                assert "send /today or /week" in invalid_draft_reply["plain_text"]
                assert "untrusted-token-content" not in invalid_draft_reply["plain_text"]
        finally:
            await engine.dispose()

    run_local_async(scenario)
