from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.infrastructure.models import ReminderIntentRecord
from pin.contexts.planning.domain.plans import OneOffPlanDraft, PlanKind, resolve_schedule
from pin.contexts.planning.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
)
from pin.contexts.planning.infrastructure.home_zone_change_models import (
    HomeZoneChangePreviewRecord,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.contexts.planning.infrastructure.series_models import SeriesOccurrenceRecord, SeriesRecord
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.platform.jobs import JobLane
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue15_zone_runtime_"
DATABASE_PORT = 51017
DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _database_url() -> str:
    value = os.getenv("PIN_TEST_HOME_ZONE_RUNTIME_DATABASE_URL")
    if not value:
        pytest.skip("set PIN_TEST_HOME_ZONE_RUNTIME_DATABASE_URL to the assigned Issue 15 fixture")
    engine = create_engine(value)
    try:
        with engine.connect() as connection:
            name = str(connection.exec_driver_sql("select current_database()").scalar_one())
            port = int(connection.exec_driver_sql("show port").scalar_one())
            user = str(connection.exec_driver_sql("select current_user").scalar_one())
            directory = str(connection.exec_driver_sql("show data_directory").scalar_one())
    finally:
        engine.dispose()
    if not name.startswith(DATABASE_PREFIX):
        pytest.fail("home-zone runtime database is outside its assigned fixture prefix")
    if port != DATABASE_PORT or user != "pin_test":
        pytest.fail("home-zone runtime PostgreSQL does not match the assigned user and port")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATA_DIRECTORY.casefold():
        pytest.fail("home-zone runtime database is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def home_zone_runtime_database_url() -> str:
    value = _database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


async def _seed_owner(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    access_ends_at: datetime = datetime(2100, 1, 1, tzinfo=UTC),
) -> UUID:
    async with sessions() as session, session.begin():
        mapped_owner = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        owner_id = mapped_owner
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"home-zone-runtime-{owner_id}",
                policy_kind="paid",
                policy_version="test-v1",
                starts_at=datetime(2020, 1, 1, tzinfo=UTC),
                ends_at=access_ends_at,
                ai_turn_limit=1000,
                voice_second_limit=3600,
            )
        )
        session.add(
            PreferencesRecord(
                owner_id=owner_id,
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
        session.add(
            OnboardingRecord(
                owner_id=owner_id,
                draft_id=uuid4(),
                mode="initial",
                step="complete",
                draft={},
                base_profile_version=1,
                version=1,
            )
        )
    return owner_id


async def _seed_task(
    session: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    at: datetime,
    day: date,
    zone: str,
) -> tuple[UUID, UUID]:
    draft = OneOffPlanDraft(
        kind=PlanKind.TASK,
        title=title,
        local_date=day,
        local_time=time(12),
        event_zone=zone,
        reminders=(),
    )
    schedule = resolve_schedule(draft, default_zone="UTC", now=at)
    plan_id, occurrence_id = uuid4(), uuid4()
    session.add(
        PlanItemRecord(id=plan_id, owner_id=owner_id, kind="task", title=title, version=1)
    )
    await session.flush()
    session.add(
        OccurrenceRecord(
            id=occurrence_id,
            owner_id=owner_id,
            plan_item_id=plan_id,
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
            reminder_preferences_version=1,
        )
    )
    await session.flush()
    return plan_id, occurrence_id


async def _seed_weekly_series(
    session: AsyncSession, *, owner_id: UUID, at: datetime, title: str = "Zone series"
) -> tuple[UUID, list[UUID]]:
    horizon = date(2026, 12, 1)
    draft = RecurringPlanDraft(
        kind=PlanKind.TASK,
        title=title,
        start_date=date(2026, 10, 19),
        local_time=time(12),
        event_zone="UTC",
        recurrence=RecurrenceRule(
            RecurrenceFrequency.WEEKLY, interval=1, weekdays=(0,), count=3
        ),
    )
    projection = project_recurrence(
        draft,
        owner_id=owner_id,
        default_zone="UTC",
        default_reminders=(),
        at=at,
        through=horizon,
    )
    await SqlAlchemySeriesStore(session).persist_series(
        owner_id=owner_id,
        action_key=f"zone-runtime-seed-{uuid4()}",
        payload_sha256="d" * 64,
        approved_preferences_version=1,
        draft=draft,
        projection=projection,
        effective_reminders=(),
        default_zone="UTC",
        quiet_zone="UTC",
        quiet_start=time(22),
        quiet_end=time(8),
        at=at,
        horizon_date=horizon,
    )
    series = await session.scalar(
        select(SeriesRecord).where(SeriesRecord.owner_id == owner_id)
    )
    assert series is not None
    occurrences = list(
        (
            await session.scalars(
                select(OccurrenceRecord)
                .join(
                    SeriesOccurrenceRecord,
                    SeriesOccurrenceRecord.occurrence_id == OccurrenceRecord.id,
                )
                .where(
                    SeriesOccurrenceRecord.owner_id == owner_id,
                    SeriesOccurrenceRecord.series_id == series.id,
                )
                .order_by(SeriesOccurrenceRecord.sequence)
            )
        ).all()
    )
    assert len(occurrences) == 3
    return series.id, [row.id for row in occurrences]


async def _persist(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    text: str | None = None,
    callback: str | None = None,
) -> None:
    update_id = uuid4().int % 9_000_000_000_000_000
    if callback is None:
        payload: dict[str, object] = {
            "update_id": update_id,
            "message": {
                "from": {"id": user_id, "is_bot": False, "first_name": "Home zone test"},
                "chat": {"id": user_id, "type": "private"},
                "date": int(datetime.now(UTC).timestamp()),
                "text": text,
            },
        }
    else:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"home-zone-{uuid4().hex}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Home zone test"},
                "message": {"message_id": 1, "chat": {"id": user_id, "type": "private"}},
                "data": callback,
            },
        }
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload=payload,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        job = await session.scalar(
            select(Job)
            .where(
                Job.owner_id == inbox.owner_id,
                Job.lane == JobLane.APPLICATION.value,
                Job.status == "pending",
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        assert job is not None
        job.priority = 2_147_483_647


async def _dispatch(
    sessions: async_sessionmaker[AsyncSession],
    *,
    bot_id: int,
    user_id: int,
    at: datetime,
    text: str | None = None,
    callback: str | None = None,
) -> tuple[UUID, dict[str, object]]:
    await _persist(sessions, bot_id=bot_id, user_id=user_id, text=text, callback=callback)
    claim = await claim_application_job(sessions, worker_id=f"home-zone-{uuid4().hex[:12]}")
    assert claim is not None
    await process_application_job(sessions, claim, now=at)
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        job = await session.get(Job, claim.job_id)
        assert job is not None and job.status == "succeeded"
        return claim.job_id, cast(dict[str, object], event.payload["payload"])


def _callback_for(reply: dict[str, object], label: str) -> str:
    markup = cast(dict[str, object], reply["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    for row in rows:
        for button in row:
            if button.get("text") == label:
                callback_data = button.get("callback_data")
                assert isinstance(callback_data, str)
                return callback_data
    raise AssertionError(f"button {label!r} was not rendered")


def _callback_containing(reply: dict[str, object], label_fragment: str) -> str:
    markup = cast(dict[str, object], reply["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    for row in rows:
        for button in row:
            callback_data = button.get("callback_data")
            if label_fragment in str(button.get("text", "")) and isinstance(callback_data, str):
                return callback_data
    raise AssertionError(f"button containing {label_fragment!r} was not rendered")


async def _callback_for_action(
    sessions: async_sessionmaker[AsyncSession],
    reply: dict[str, object],
    *,
    owner_id: UUID,
    action: str,
) -> str:
    markup = cast(dict[str, object], reply["reply_markup"])
    rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
    async with sessions() as session:
        for row in rows:
            for button in row:
                token = button.get("callback_data")
                if not isinstance(token, str):
                    continue
                token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
                binding = await session.get(TelegramCallbackTokenRecord, token_hash)
                if (
                    binding is not None
                    and binding.owner_id == owner_id
                    and binding.action == action
                ):
                    return token
    raise AssertionError(f"no rendered callback for action {action!r}")


async def _prepare_zone_change(
    sessions: async_sessionmaker[AsyncSession], *, bot_id: int, user_id: int, at: datetime
) -> dict[str, object]:
    _, reply = await _dispatch(sessions, bot_id=bot_id, user_id=user_id, at=at, text="/settings")
    assert reply["handling"] == "handled"
    for text in ("keep", "America/New_York", "keep", "keep"):
        _, reply = await _dispatch(sessions, bot_id=bot_id, user_id=user_id, at=at, text=text)
    assert "confirm" in str(reply["plain_text"]).casefold()
    _, review = await _dispatch(sessions, bot_id=bot_id, user_id=user_id, at=at, text="confirm")
    return review


def test_settings_zone_change_requires_keep_preview_and_atomic_confirmation(
    home_zone_runtime_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 6_100_000_000 + suffix, 7_100_000_000 + suffix
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=user_id)
            review = await _prepare_zone_change(sessions, bot_id=bot_id, user_id=user_id, at=at)
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.home_zone == "UTC" and profile.version == 1
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "confirm"
                assert draft.draft.get("home_zone") == "America/New_York"
                assert "_home_zone_change_flow" in draft.draft
                assert (
                    await session.scalar(
                        select(HomeZoneChangePreviewRecord.id).where(
                            HomeZoneChangePreviewRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
            keep_token = _callback_for(review, "Keep local schedules")
            _, preview = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=keep_token,
            )
            confirm_token = _callback_for(preview, "Confirm zone change")
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.home_zone == "UTC" and profile.version == 1
                saved = await session.scalar(
                    select(HomeZoneChangePreviewRecord).where(
                        HomeZoneChangePreviewRecord.owner_id == owner_id
                    )
                )
                assert saved is not None and saved.state == "pending"

            _, confirmed = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm_token,
            )
            assert confirmed["handling"] == "handled"
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                draft = await session.get(OnboardingRecord, owner_id)
                saved = await session.scalar(
                    select(HomeZoneChangePreviewRecord).where(
                        HomeZoneChangePreviewRecord.owner_id == owner_id
                    )
                )
                assert profile is not None
                assert profile.home_zone == "America/New_York" and profile.version == 2
                assert draft is not None and draft.step == "complete"
                assert saved is not None and saved.state == "confirmed"
                handoffs = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(
                                OutboxEvent.owner_id == owner_id,
                                OutboxEvent.event_type == "identity.home_zone_change.requested",
                            )
                        )
                    ).all()
                )
                assert len(handoffs) == 1
                assert handoffs[0].aggregate_version == 2
                assert (
                    await session.scalar(
                        select(ReminderIntentRecord.id).where(
                            ReminderIntentRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
                assert (
                    await session.scalar(
                        select(ProviderCallRecord.call_id).where(
                            ProviderCallRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_home_zone_cancel_leaves_profile_unchanged(
    home_zone_runtime_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 6_200_000_000 + suffix, 7_200_000_000 + suffix
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=user_id)
            review = await _prepare_zone_change(sessions, bot_id=bot_id, user_id=user_id, at=at)
            _, cancelled = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_for(review, "Cancel settings"),
            )
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                draft = await session.get(OnboardingRecord, owner_id)
                assert profile is not None and profile.home_zone == "UTC" and profile.version == 1
                assert draft is None
                assert "Settings cancelled" in str(cancelled["plain_text"])
                assert (
                    await session.scalar(
                        select(HomeZoneChangePreviewRecord.id).where(
                            HomeZoneChangePreviewRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_reinterprets_only_selected_occurrence_and_preserves_other_zone(
    home_zone_runtime_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 6_300_000_000 + suffix, 7_300_000_000 + suffix
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=user_id)
            async with sessions() as session, session.begin():
                _, selected_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Selected home-zone occurrence",
                    at=at,
                    day=date(2026, 10, 20),
                    zone="UTC",
                )
                _, preserved_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Unselected Paris occurrence",
                    at=at,
                    day=date(2026, 10, 21),
                    zone="Europe/Paris",
                )
                preserved_before = await session.get(OccurrenceRecord, preserved_id)
                assert preserved_before is not None
                preserved_snapshot = (
                    preserved_before.local_date,
                    preserved_before.local_time,
                    preserved_before.event_zone,
                    preserved_before.starts_at,
                )

            review = await _prepare_zone_change(sessions, bot_id=bot_id, user_id=user_id, at=at)
            _, scope_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_for(review, "Choose plans"),
            )
            _, selected_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_containing(scope_menu, "Selected home-zone occurrence"),
            )
            assert "Selected: 1 occurrence(s)" in str(selected_menu["plain_text"])
            _, preview = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_for(selected_menu, "Preview selected"),
            )
            preview_text = str(preview["plain_text"])
            assert "Selected home-zone occurrence" in preview_text
            assert "UTC →" in preview_text and "America/New_York" in preview_text
            confirm_token = _callback_for(preview, "Confirm zone change")

            async with sessions() as session:
                pending = await session.get(OccurrenceRecord, selected_id)
                assert pending is not None
                assert (pending.event_zone, pending.local_date, pending.local_time) == (
                    "UTC",
                    date(2026, 10, 20),
                    time(12),
                )
                assert pending.starts_at == datetime(2026, 10, 20, 12, tzinfo=UTC)

            _, confirmed = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm_token,
            )
            assert confirmed["handling"] == "handled"
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                selected = await session.get(OccurrenceRecord, selected_id)
                preserved = await session.get(OccurrenceRecord, preserved_id)
                assert profile is not None and (
                    profile.home_zone,
                    profile.version,
                ) == ("America/New_York", 2)
                assert selected is not None and (
                    selected.version,
                    selected.local_date,
                    selected.local_time,
                    selected.event_zone,
                    selected.starts_at,
                ) == (
                    2,
                    date(2026, 10, 20),
                    time(12),
                    "America/New_York",
                    datetime(2026, 10, 20, 16, tzinfo=UTC),
                )
                assert preserved is not None and (
                    preserved.local_date,
                    preserved.local_time,
                    preserved.event_zone,
                    preserved.starts_at,
                ) == preserved_snapshot
                assert (
                    await session.scalar(
                        select(ProviderCallRecord.call_id).where(
                            ProviderCallRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_reinterprets_selected_series_and_preserves_unselected_schedule(
    home_zone_runtime_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id, user_id = 6_400_000_000 + suffix, 7_400_000_000 + suffix
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=user_id)
            async with sessions() as session, session.begin():
                series_id, series_occurrence_ids = await _seed_weekly_series(
                    session, owner_id=owner_id, at=at
                )
                _, preserved_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Unselected standalone task",
                    at=at,
                    day=date(2026, 10, 22),
                    zone="Europe/Paris",
                )
                series_before = await session.get(SeriesRecord, series_id)
                preserved_before = await session.get(OccurrenceRecord, preserved_id)
                assert series_before is not None and preserved_before is not None
                anchor_before = series_before.anchor_date
                count_before = series_before.total_count
                preserved_snapshot = (
                    preserved_before.local_date,
                    preserved_before.local_time,
                    preserved_before.event_zone,
                    preserved_before.starts_at,
                )

            review = await _prepare_zone_change(sessions, bot_id=bot_id, user_id=user_id, at=at)
            _, scope_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_for(review, "Choose plans"),
            )
            _, selected_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=await _callback_for_action(
                    sessions,
                    scope_menu,
                    owner_id=owner_id,
                    action="home_zone_toggle_series",
                ),
            )
            assert "Selected: 0 occurrence(s), 1 series" in str(selected_menu["plain_text"])
            _, preview = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=_callback_for(selected_menu, "Preview selected"),
            )
            preview_text = str(preview["plain_text"])
            assert "Zone series" in preview_text
            assert "UTC → America/New_York" in preview_text
            confirm_token = _callback_for(preview, "Confirm zone change")

            async with sessions() as session:
                for occurrence_id in series_occurrence_ids:
                    row = await session.get(OccurrenceRecord, occurrence_id)
                    assert row is not None and row.event_zone == "UTC" and row.version == 1

            _, confirmed = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=user_id,
                at=at,
                callback=confirm_token,
            )
            assert confirmed["handling"] == "handled"
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                series = await session.get(SeriesRecord, series_id)
                preserved = await session.get(OccurrenceRecord, preserved_id)
                assert profile is not None and profile.home_zone == "America/New_York"
                assert series is not None
                assert (
                    series.version,
                    series.anchor_date,
                    series.total_count,
                    series.typed_draft["default_zone"],
                ) == (2, anchor_before, count_before, "America/New_York")
                for occurrence_id in series_occurrence_ids:
                    row = await session.get(OccurrenceRecord, occurrence_id)
                    assert row is not None
                    assert (row.event_zone, row.local_time, row.version) == (
                        "America/New_York",
                        time(12),
                        2,
                    )
                    assert row.starts_at == datetime.combine(
                        row.local_date, time(12), tzinfo=ZoneInfo("America/New_York")
                    ).astimezone(UTC)
                assert preserved is not None and (
                    preserved.local_date,
                    preserved.local_time,
                    preserved.event_zone,
                    preserved.starts_at,
                ) == preserved_snapshot
                assert (
                    await session.scalar(
                        select(ProviderCallRecord.call_id).where(
                            ProviderCallRecord.owner_id == owner_id
                        )
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_reinterpretation_fails_honestly_when_access_is_expired(
    home_zone_runtime_database_url: str,
) -> None:
    async def select_preview(
        sessions: async_sessionmaker[AsyncSession],
        *,
        bot_id: int,
        user_id: int,
        title: str,
        at: datetime,
    ) -> dict[str, object]:
        review = await _prepare_zone_change(sessions, bot_id=bot_id, user_id=user_id, at=at)
        _, scope_menu = await _dispatch(
            sessions,
            bot_id=bot_id,
            user_id=user_id,
            at=at,
            callback=_callback_for(review, "Choose plans"),
        )
        _, selected_menu = await _dispatch(
            sessions,
            bot_id=bot_id,
            user_id=user_id,
            at=at,
            callback=_callback_containing(scope_menu, title),
        )
        assert "Selected: 1 occurrence(s)" in str(selected_menu["plain_text"])
        _, preview = await _dispatch(
            sessions,
            bot_id=bot_id,
            user_id=user_id,
            at=at,
            callback=_callback_for(selected_menu, "Preview selected"),
        )
        return preview

    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        try:
            expired_bot, expired_user = 6_500_000_000 + suffix, 7_500_000_000 + suffix
            expired_owner = await _seed_owner(
                sessions,
                bot_id=expired_bot,
                user_id=expired_user,
                access_ends_at=at - timedelta(minutes=1),
            )
            async with sessions() as session, session.begin():
                _, expired_occurrence = await _seed_task(
                    session,
                    owner_id=expired_owner,
                    title="Expired access occurrence",
                    at=at,
                    day=date(2026, 10, 20),
                    zone="UTC",
                )

            denied_preview = await select_preview(
                sessions,
                bot_id=expired_bot,
                user_id=expired_user,
                title="Expired access occurrence",
                at=at,
            )
            assert "changed or expired" in str(denied_preview["plain_text"])
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, expired_owner)
                occurrence = await session.get(OccurrenceRecord, expired_occurrence)
                previews = (
                    await session.scalars(
                        select(HomeZoneChangePreviewRecord).where(
                            HomeZoneChangePreviewRecord.owner_id == expired_owner
                        )
                    )
                ).all()
                planning_events = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.owner_id == expired_owner,
                        OutboxEvent.event_type.like("planning.%"),
                    )
                )
                assert profile is not None and (profile.home_zone, profile.version) == ("UTC", 1)
                assert occurrence is not None and (occurrence.event_zone, occurrence.version) == (
                    "UTC",
                    1,
                )
                assert occurrence.local_date == date(2026, 10, 20)
                # Settings may already have saved Keep-only previews while the
                # user chose a zone and scope. The denied reinterpret choice
                # must not persist a preview that includes this target.
                assert all(
                    str(expired_occurrence)
                    not in record.payload.get("reinterpret_occurrence_ids", [])
                    and not record.payload.get("reinterpret_series_ids", [])
                    and not record.payload.get("reminder_overrides", {})
                    for record in previews
                )
                assert planning_events == 0

            expiring_bot, expiring_user = 6_600_000_000 + suffix, 7_600_000_000 + suffix
            expiring_owner = await _seed_owner(
                sessions,
                bot_id=expiring_bot,
                user_id=expiring_user,
                access_ends_at=at + timedelta(minutes=1),
            )
            async with sessions() as session, session.begin():
                _, expiring_occurrence = await _seed_task(
                    session,
                    owner_id=expiring_owner,
                    title="Expiring confirmation occurrence",
                    at=at,
                    day=date(2026, 10, 21),
                    zone="UTC",
                )

            live_preview = await select_preview(
                sessions,
                bot_id=expiring_bot,
                user_id=expiring_user,
                title="Expiring confirmation occurrence",
                at=at,
            )
            if "Confirm zone change" not in {
                button.get("text")
                for row in cast(
                    list[list[dict[str, object]]],
                    cast(dict[str, object], live_preview["reply_markup"])["inline_keyboard"],
                )
                for button in row
            }:
                # A legacy occurrence can lack an approved reminder projection.
                # The owner explicitly chooses the reviewed reminder settings
                # before the complete zone preview becomes confirmable.
                _, live_preview = await _dispatch(
                    sessions,
                    bot_id=expiring_bot,
                    user_id=expiring_user,
                    at=at,
                    callback=_callback_for(
                        live_preview, "Use reviewed reminders for these items"
                    ),
                )
            confirm_callback = _callback_for(live_preview, "Confirm zone change")
            async with sessions() as session:
                saved_preview = await session.scalar(
                    select(HomeZoneChangePreviewRecord).where(
                        HomeZoneChangePreviewRecord.owner_id == expiring_owner
                    )
                )
                assert saved_preview is not None and saved_preview.state == "pending"

            late_confirmation = at + timedelta(minutes=2)
            _, denied_confirmation = await _dispatch(
                sessions,
                bot_id=expiring_bot,
                user_id=expiring_user,
                at=late_confirmation,
                callback=confirm_callback,
            )
            assert "changed or expired" in str(denied_confirmation["plain_text"])
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, expiring_owner)
                occurrence = await session.get(OccurrenceRecord, expiring_occurrence)
                saved_preview = await session.get(HomeZoneChangePreviewRecord, saved_preview.id)
                planning_events = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.owner_id == expiring_owner,
                        OutboxEvent.event_type.like("planning.%"),
                    )
                )
                handoffs = await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.owner_id == expiring_owner,
                        OutboxEvent.event_type == "identity.home_zone_change.requested",
                    )
                )
                assert profile is not None and (profile.home_zone, profile.version) == ("UTC", 1)
                assert occurrence is not None and (
                    occurrence.event_zone,
                    occurrence.local_date,
                    occurrence.local_time,
                    occurrence.version,
                    occurrence.reminder_preferences_version,
                ) == ("UTC", date(2026, 10, 21), time(12), 1, 1)
                assert saved_preview is not None and saved_preview.state == "pending"
                assert planning_events == 0 and handoffs == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_foreign_owner_and_corrected_preview_tokens_do_not_write_plans(
    home_zone_runtime_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(home_zone_runtime_database_url)
        sessions = create_session_factory(engine)
        at = datetime(2026, 10, 14, 9, tzinfo=UTC)
        suffix = uuid4().int % 1_000_000_000
        bot_id = 6_500_000_000 + suffix
        owner_user, foreign_user = 7_500_000_000 + suffix, 8_500_000_000 + suffix
        try:
            owner_id = await _seed_owner(sessions, bot_id=bot_id, user_id=owner_user)
            foreign_owner = await _seed_owner(
                sessions, bot_id=bot_id, user_id=foreign_user
            )
            async with sessions() as session, session.begin():
                _, target_id = await _seed_task(
                    session,
                    owner_id=owner_id,
                    title="Owner selected item",
                    at=at,
                    day=date(2026, 10, 20),
                    zone="UTC",
                )

            review = await _prepare_zone_change(
                sessions, bot_id=bot_id, user_id=owner_user, at=at
            )
            _, scope_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=_callback_for(review, "Choose plans"),
            )
            _, selected_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=_callback_containing(scope_menu, "Owner selected item"),
            )
            toggle_callback = _callback_containing(selected_menu, "Owner selected item")
            _, preview = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=_callback_for(selected_menu, "Preview selected"),
            )
            confirm_token = _callback_for(preview, "Confirm zone change")

            _, foreign_reply = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=foreign_user,
                at=at,
                callback=confirm_token,
            )
            assert "Confirm zone change" not in str(foreign_reply["plain_text"])
            async with sessions() as session:
                foreign_profile = await session.get(PreferencesRecord, foreign_owner)
                assert foreign_profile is not None and (
                    foreign_profile.home_zone,
                    foreign_profile.version,
                ) == ("UTC", 1)

            _, corrected_menu = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=toggle_callback,
            )
            assert "Selected: 0 occurrence(s)" in str(corrected_menu["plain_text"])
            _, stale_reply = await _dispatch(
                sessions,
                bot_id=bot_id,
                user_id=owner_user,
                at=at,
                callback=confirm_token,
            )
            assert "changed" in str(stale_reply["plain_text"]).lower() or "expired" in str(
                stale_reply["plain_text"]
            ).lower()

            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                occurrence = await session.get(OccurrenceRecord, target_id)
                assert profile is not None and (profile.home_zone, profile.version) == (
                    "UTC",
                    1,
                )
                assert occurrence is not None and (
                    occurrence.state,
                    occurrence.version,
                    occurrence.local_date,
                    occurrence.local_time,
                    occurrence.event_zone,
                    occurrence.starts_at,
                ) == (
                    "open",
                    1,
                    date(2026, 10, 20),
                    time(12),
                    "UTC",
                    datetime(2026, 10, 20, 12, tzinfo=UTC),
                )
                assert (
                    await session.scalar(
                        select(OutboxEvent.id).where(
                            OutboxEvent.owner_id == owner_id,
                            OutboxEvent.event_type == "identity.home_zone_change.requested",
                        )
                    )
                    is None
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)
