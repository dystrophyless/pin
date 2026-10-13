from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver, persist_update
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.notifications.infrastructure.models import ReminderIntentRecord
from pin.contexts.planning.infrastructure.home_zone_change_models import (
    HomeZoneChangePreviewRecord,
)
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
    sessions: async_sessionmaker[AsyncSession], *, bot_id: int, user_id: int
) -> UUID:
    async with sessions() as session, session.begin():
        mapped_owner = await PostgresTelegramOwnerResolver().resolve(
            session, bot_id=bot_id, telegram_user_id=user_id
        )
        owner_id = mapped_owner
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
