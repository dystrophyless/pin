from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.adapters.telegram.onboarding import handle_private_text
from pin.contexts.billing.api import SqlAlchemyAccessUsageStore, StartTrial
from pin.contexts.billing.infrastructure.models import (
    AccessPeriodRecord,
    TrialActivationRecord,
)
from pin.contexts.identity.application.onboarding import (
    answer_onboarding,
    begin_onboarding,
    confirm_onboarding,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.models import OnboardingRecord, PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.models import OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("PIN_TEST_ONBOARDING_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_TEST_ONBOARDING_DATABASE_URL to the owned disposable PostgreSQL database",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def onboarding_database() -> Iterator[str]:
    assert DATABASE_URL is not None
    command.upgrade(_alembic_config(DATABASE_URL), "head")
    yield DATABASE_URL


def test_private_onboarding_survives_restarts_and_activates_one_trial(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = None
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        try:

            async def send(update_id: int, text: str) -> None:
                nonlocal owner_id
                now = datetime.now(UTC)
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload={
                            "update_id": update_id,
                            "message": {
                                "message_id": update_id,
                                "date": int(now.timestamp()),
                                "chat": {"id": telegram_user_id, "type": "private"},
                                "from": {"id": telegram_user_id, "is_bot": False},
                                "text": text,
                            },
                        },
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                    mapped_owner_id = await session.scalar(
                        select(TelegramOwnerRecord.owner_id).where(
                            TelegramOwnerRecord.bot_id == bot_id,
                            TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                        )
                    )
                    assert mapped_owner_id is not None
                    if owner_id is None:
                        owner_id = mapped_owner_id
                    assert mapped_owner_id == owner_id
                claim = await claim_application_job(sessions, worker_id="onboarding-test")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)

            await send(1, "/start")
            async with sessions() as session:
                assert owner_id is not None
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "language"

            # Each message is a new durable job and every assertion reads after the prior
            # worker session has committed, exercising restart-safe continuation.
            await send(2, "ru")
            async with sessions() as session:
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "home_zone"
            await send(3, "Asia/Almaty")
            async with sessions() as session:
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "reminders"
            await send(4, "suggested")
            async with sessions() as session:
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "quiet_review"
            await send(5, "suggested")
            async with sessions() as session:
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "confirm"
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is None
            await send(6, "confirm")

            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                draft = await session.get(OnboardingRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
                assert profile is not None
                assert (profile.language, profile.home_zone, profile.version) == (
                    "ru",
                    "Asia/Almaty",
                    1,
                )
                assert profile.reminder_calendar_days == profile.reminder_elapsed_hours == 1
                assert profile.quiet_start.isoformat() == "22:00:00"
                assert profile.quiet_end.isoformat() == "08:00:00"
                assert profile.review_time.isoformat() == "20:00:00"
                assert draft is not None and draft.step == "complete"
                assert trial is not None and trial.telegram_user_id == telegram_user_id
                events = await _owner_events(session, owner_id)
                replies = [
                    event
                    for event in events
                    if event.owner_id == owner_id and event.event_type == "telegram.reply.requested"
                ]
                assert len(replies) == 6
                assert "Пробный период начат" in replies[-1].payload["plain_text"]
                assert replies[-1].payload["chat_id"] == telegram_user_id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(
                            OutboxEvent.owner_id == owner_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                        )
                    )
                    == 6
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(
                            OutboxEvent.owner_id == owner_id,
                            OutboxEvent.event_type == "billing.access_period.activated",
                        )
                    )
                    == 1
                )

            # /start is read-only for a retained profile and does not reset its trial.
            await send(7, "/start")
            await send(8, "Tomorrow at 5 pm, review the project")
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.version == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 1
                )
                events = await _owner_events(session, owner_id)
                last_reply = next(
                    event
                    for event in reversed(events)
                    if event.owner_id == owner_id and event.event_type == "telegram.reply.requested"
                )
                assert last_reply.payload["handling"] == "unhandled"
                assert "не сохранено" in last_reply.payload["plain_text"]

        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_cancel_and_preview_are_non_mutating_and_zone_change_is_handed_off(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = None
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        try:

            async def send(
                update_id: int, text: str | None = None, *, callback: str | None = None
            ) -> dict[str, object]:
                nonlocal owner_id
                now = datetime.now(UTC)
                if callback is None:
                    update_payload: dict[str, object] = {
                        "update_id": update_id,
                        "message": {
                            "message_id": update_id,
                            "date": int(now.timestamp()),
                            "chat": {"id": telegram_user_id, "type": "private"},
                            "from": {"id": telegram_user_id, "is_bot": False},
                            "text": text,
                        },
                    }
                else:
                    update_payload = {
                        "update_id": update_id,
                        "callback_query": {
                            "id": f"onboarding-zone-{uuid4().hex}",
                            "from": {"id": telegram_user_id, "is_bot": False},
                            "message": {
                                "message_id": update_id,
                                "chat": {"id": telegram_user_id, "type": "private"},
                            },
                            "data": callback,
                        },
                    }
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=update_payload,
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                    mapped_owner_id = await session.scalar(
                        select(TelegramOwnerRecord.owner_id).where(
                            TelegramOwnerRecord.bot_id == bot_id,
                            TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                        )
                    )
                    assert mapped_owner_id is not None
                    if owner_id is None:
                        owner_id = mapped_owner_id
                    assert mapped_owner_id == owner_id
                claim = await claim_application_job(sessions, worker_id="onboarding-test")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)
                async with sessions() as session:
                    event = await session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.causation_id == claim.job_id,
                        )
                    )
                    assert event is not None
                    return cast(dict[str, object], event.payload["payload"])

            def callback_for(reply: dict[str, object], label: str) -> str:
                markup = cast(dict[str, object], reply["reply_markup"])
                rows = cast(list[list[dict[str, object]]], markup["inline_keyboard"])
                for row in rows:
                    for button in row:
                        if button.get("text") == label:
                            data = button.get("callback_data")
                            assert isinstance(data, str)
                            return data
                raise AssertionError(f"button {label!r} was not rendered")

            await send(100, "/start")
            await send(101, "/cancel")
            async with sessions() as session:
                assert await session.get(PreferencesRecord, owner_id) is None
                assert await session.get(OnboardingRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None

            # Start afresh, confirm, then cancel a preview and ensure the saved state remains.
            for offset, text in enumerate(
                ("/start", "en", "Asia/Almaty", "suggested", "suggested", "confirm"),
                start=102,
            ):
                await send(offset, text)
            async with sessions() as session:
                baseline = await session.get(PreferencesRecord, owner_id)
                assert baseline is not None
                baseline_version = baseline.version
                baseline_zone = baseline.home_zone
            await send(108, "/settings")
            await send(109, "keep")
            await send(110, "Asia/Aqtobe")
            await send(111, "/cancel")
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None
                assert profile.version == baseline_version
                assert profile.home_zone == baseline_zone
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 1
                )

            # A confirmed home-zone edit publishes a versioned downstream handoff.
            await send(112, "/settings")
            await send(113, "keep")
            await send(114, "Asia/Aqtobe")
            await send(115, "keep")
            await send(116, "keep")
            review = await send(117, "confirm")
            async with sessions() as session:
                before_confirmation = await session.get(PreferencesRecord, owner_id)
                assert before_confirmation is not None
                assert before_confirmation.home_zone == baseline_zone
                assert before_confirmation.version == baseline_version
                assert not [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.owner_id == owner_id
                    and event.event_type == "identity.home_zone_change.requested"
                ]
            preview = await send(
                126, callback=callback_for(review, "Keep local schedules")
            )
            async with sessions() as session:
                still_pending = await session.get(PreferencesRecord, owner_id)
                assert still_pending is not None
                assert still_pending.home_zone == baseline_zone
                assert still_pending.version == baseline_version
            await send(127, callback=callback_for(preview, "Confirm zone change"))
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None
                assert profile.home_zone == "Asia/Aqtobe"
                assert profile.version == baseline_version + 1
                handoffs = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.owner_id == owner_id
                    and event.event_type == "identity.home_zone_change.requested"
                ]
                assert len(handoffs) == 1
                assert handoffs[0].aggregate_version == profile.version
                assert handoffs[0].payload == {
                    "previous_home_zone": "Asia/Almaty",
                    "requested_home_zone": "Asia/Aqtobe",
                    "preferences_version": profile.version,
                }
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 1
                )

            # A newer version committed elsewhere wins; this preview cannot overwrite it.
            await send(118, "/settings")
            await send(119, "keep")
            await send(120, "keep")
            await send(121, "keep")
            await send(122, "keep")
            async with sessions() as session, session.begin():
                current = await session.get(PreferencesRecord, owner_id)
                assert current is not None
                newer = Preferences(
                    language=current.language,
                    home_zone=current.home_zone,
                    reminder_calendar_days=current.reminder_calendar_days,
                    reminder_elapsed_hours=current.reminder_elapsed_hours,
                    quiet_start=current.quiet_start,
                    quiet_end=current.quiet_end,
                    review_time=current.review_time.replace(hour=19),
                    version=current.version + 1,
                )
                saved = await SqlAlchemyIdentityStore(session).save_preferences(
                    owner_id=owner_id,
                    expected_version=current.version,
                    preferences=newer,
                )
                assert saved is not None
            await send(123, "confirm")
            async with sessions() as session:
                current = await session.get(PreferencesRecord, owner_id)
                assert current is not None
                assert current.version == baseline_version + 2
                assert current.review_time.hour == 19
                assert await session.get(OnboardingRecord, owner_id) is None
            await send(124, "/settings")
            async with sessions() as session:
                refreshed = await session.get(OnboardingRecord, owner_id)
                assert refreshed is not None
                assert refreshed.base_profile_version == baseline_version + 2
                assert refreshed.draft["review_time"] == "19:00:00"
            await send(125, "/cancel")
            async with sessions() as session:
                assert await session.get(OnboardingRecord, owner_id) is None
            plan_cancel = await send(128, "/cancel")
            assert "There are no open tasks for this action." in str(plan_cancel["plain_text"])
            assert plan_cancel["handling"] == "handled"
        finally:
            await engine.dispose()

    run_local_async(scenario)


async def _owner_events(session, owner_id) -> list[EventEnvelope]:
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.owner_id == owner_id)
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
    )
    return [EventEnvelope.from_dict(row.payload) for row in rows]


def test_profile_and_billing_trial_roll_back_in_the_same_unit_of_work(
    onboarding_database: str,
) -> None:
    class FailAfterTrialWrite:
        def __init__(self, session) -> None:
            self.session = session

        async def activate_trial(self, *, owner_id, telegram_user_id, at):
            await SqlAlchemyAccessUsageStore(self.session).activate_trial(
                StartTrial(owner_id, telegram_user_id, at)
            )
            raise RuntimeError("simulate transaction failure after Billing writes")

    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = uuid4().int % 2**52 or 1
        try:
            async with sessions() as session, session.begin():
                await begin_onboarding(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    now=datetime.now(UTC),
                )
            for text in ("en", "Asia/Almaty", "suggested", "suggested"):
                async with sessions() as session, session.begin():
                    store = SqlAlchemyIdentityStore(session)
                    await handle_private_text(
                        store,
                        owner_id=owner_id,
                        telegram_user_id=telegram_user_id,
                        text=text,
                        now=datetime.now(UTC),
                        trial_activator=FailAfterTrialWrite(session),
                    )
            with pytest.raises(RuntimeError, match="simulate transaction failure"):
                async with sessions() as session, session.begin():
                    await handle_private_text(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        telegram_user_id=telegram_user_id,
                        text="confirm",
                        now=datetime.now(UTC),
                        trial_activator=FailAfterTrialWrite(session),
                    )
            async with sessions() as session:
                assert await session.get(PreferencesRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AccessPeriodRecord)
                        .where(AccessPeriodRecord.owner_id == owner_id)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_invalid_answers_leave_their_step_unchanged(onboarding_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                await begin_onboarding(store, owner_id=owner_id, now=datetime.now(UTC))
                assert (
                    await answer_onboarding(store, owner_id=owner_id, text="ru")
                ).step == "home_zone"
                invalid_zone = await answer_onboarding(
                    store, owner_id=owner_id, text="Mars/Olympus"
                )
                assert invalid_zone.state == "invalid_input" and invalid_zone.step == "home_zone"
                assert (
                    await answer_onboarding(store, owner_id=owner_id, text="Asia/Almaty")
                ).step == "reminders"
                for invalid_offset in ("-1, 1", "one, two", "-1 hours"):
                    invalid = await answer_onboarding(store, owner_id=owner_id, text=invalid_offset)
                    assert invalid.state == "invalid_input" and invalid.step == "reminders"
                assert (
                    await answer_onboarding(store, owner_id=owner_id, text="suggested")
                ).step == "quiet_review"
                for invalid_hours in (
                    "22:00-22:00; 20:00",
                    "25:00-08:00; 20:00",
                    "22:00-08:00; 25:00",
                ):
                    invalid = await answer_onboarding(store, owner_id=owner_id, text=invalid_hours)
                    assert invalid.state == "invalid_input" and invalid.step == "quiet_review"
                assert await store.get_preferences(owner_id) is None
                assert await store.get_onboarding(owner_id) is not None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_simultaneous_confirmations_create_one_profile_and_one_trial(
    onboarding_database: str,
) -> None:
    class TrialActivator:
        def __init__(self, session) -> None:
            self._store = SqlAlchemyAccessUsageStore(session)

        async def activate_trial(self, *, owner_id, telegram_user_id, at):
            return await self._store.activate_trial(StartTrial(owner_id, telegram_user_id, at))

    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = uuid4().int % 2**52 or 1
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                await begin_onboarding(store, owner_id=owner_id, now=datetime.now(UTC))
                for text in ("en", "Asia/Almaty", "suggested", "suggested"):
                    await answer_onboarding(store, owner_id=owner_id, text=text)

            async def confirm_once():
                async with sessions() as session, session.begin():
                    return await confirm_onboarding(
                        SqlAlchemyIdentityStore(session),
                        TrialActivator(session),
                        owner_id=owner_id,
                        telegram_user_id=telegram_user_id,
                        now=datetime.now(UTC),
                    )

            results = await asyncio.gather(confirm_once(), confirm_once())
            assert sum(result.state == "confirmed" for result in results) == 1
            async with sessions() as session:
                assert await session.get(PreferencesRecord, owner_id) is not None
                assert await session.get(TrialActivationRecord, owner_id) is not None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_private_quick_replies_are_owner_bound_versioned_and_single_use(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        telegram_user_id = uuid4().int % 2**52 or 1
        foreign_telegram_user_id = telegram_user_id + 1
        bot_id = 987654 + uuid4().int % 1_000_000
        owner_id = None
        try:

            async def dispatch(update_id: int, update: dict[str, object]) -> None:
                nonlocal owner_id
                now = datetime.now(UTC)
                update["update_id"] = update_id
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=update,
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                    if owner_id is None:
                        owner_id = await session.scalar(
                            select(TelegramOwnerRecord.owner_id).where(
                                TelegramOwnerRecord.bot_id == bot_id,
                                TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                            )
                        )
                claim = await claim_application_job(sessions, worker_id="quick-reply-test")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)

            async def message_update(update_id: int, text: str) -> None:
                await message_as(update_id, telegram_user_id, text)

            async def message_as(update_id: int, actor_id: int, text: str) -> None:
                await dispatch(
                    update_id,
                    {
                        "message": {
                            "message_id": update_id,
                            "chat": {"id": actor_id, "type": "private"},
                            "from": {"id": actor_id, "is_bot": False},
                            "text": text,
                        }
                    },
                )

            async def click(update_id: int, actor_id: int, token: str) -> None:
                await dispatch(
                    update_id,
                    {
                        "callback_query": {
                            "id": f"callback-{update_id}",
                            "from": {"id": actor_id, "is_bot": False},
                            "message": {
                                "message_id": update_id,
                                "chat": {"id": actor_id, "type": "private"},
                            },
                            "data": token,
                        }
                    },
                )

            await message_update(1_000, "/start")
            assert owner_id is not None
            async with sessions() as session:
                start_reply = next(
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                )
                keyboard = start_reply.payload["reply_markup"]
                assert isinstance(keyboard, dict)
                russian_token = keyboard["inline_keyboard"][0][1]["callback_data"]
                english_token = keyboard["inline_keyboard"][0][0]["callback_data"]
                assert isinstance(russian_token, str) and isinstance(english_token, str)

            await click(1_001, foreign_telegram_user_id, russian_token)
            await click(1_002, telegram_user_id, russian_token)
            await click(1_003, telegram_user_id, russian_token)  # duplicate use
            await click(1_004, telegram_user_id, english_token)  # stale version/step
            await click(1_005, telegram_user_id, "forged-token")

            async with sessions() as session:
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "home_zone"
                assert draft.draft["language"] == "ru"
                assert await session.get(PreferencesRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None
                replies = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ]
                assert len(replies) == 5  # /start plus four original-owner callback outcomes
                assert "часовой пояс" in replies[1].payload["plain_text"]

            # Complete setup with the requested values; the profile's Edit button must
            # work even though the completed onboarding row is retained.
            for update_id, text in (
                (1_006, "Asia/Almaty"),
                (1_007, "suggested"),
                (1_008, "suggested"),
                (1_009, "подтверждаю"),
            ):
                await message_update(update_id, text)
            async with sessions() as session:
                completed = await session.get(PreferencesRecord, owner_id)
                assert completed is not None and completed.version == 1
                profile_reply = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ][-1]
                profile_keyboard = profile_reply.payload["reply_markup"]
                settings_token = next(
                    button["callback_data"]
                    for row in profile_keyboard["inline_keyboard"]
                    for button in row
                    if button["text"] == "Изменить настройки"
                )
            await click(1_010, telegram_user_id, settings_token)
            async with sessions() as session:
                preview = await session.get(OnboardingRecord, owner_id)
                assert preview is not None and preview.mode == "preferences"
                assert preview.step == "language"

            # Cancel and recreate a preview at the same step/version. An old confirm
            # button must not apply to the new draft (draft UUID prevents ABA reuse).
            for update_id, text in (
                (1_011, "en"),
                (1_012, "keep"),
                (1_013, "keep"),
                (1_014, "keep"),
                (1_015, "keep"),
            ):
                await message_update(update_id, text)
            async with sessions() as session:
                preview_reply = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ][-1]
                preview_keyboard = preview_reply.payload["reply_markup"]
                stale_confirm = next(
                    button["callback_data"]
                    for row in preview_keyboard["inline_keyboard"]
                    for button in row
                    if button["text"] == "Confirm"
                )
            await message_update(1_016, "/cancel")
            await message_update(1_017, "/settings")
            for update_id, text in (
                (1_018, "en"),
                (1_019, "keep"),
                (1_020, "keep"),
                (1_021, "keep"),
                (1_022, "keep"),
            ):
                await message_update(update_id, text)
            await click(1_023, telegram_user_id, stale_confirm)
            async with sessions() as session:
                recreated = await session.get(OnboardingRecord, owner_id)
                current_profile = await session.get(PreferencesRecord, owner_id)
                assert recreated is not None and recreated.mode == "preferences"
                assert recreated.step == "confirm"
                assert current_profile is not None and current_profile.version == 1
                assert current_profile.home_zone == "Asia/Almaty"

            # The preferences-only Keep current button for the home-zone step is
            # a valid answer callback and remains single-use.
            await message_update(1_024, "/cancel")
            await message_update(1_025, "/settings")
            async with sessions() as session:
                assert owner_id is not None
                language_reply = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ][-1]
                language_keyboard = language_reply.payload["reply_markup"]
                english_token = next(
                    button["callback_data"]
                    for row in language_keyboard["inline_keyboard"]
                    for button in row
                    if button["text"] == "English"
                )
            await click(1_026, telegram_user_id, english_token)
            async with sessions() as session:
                zone_reply = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ][-1]
                zone_keyboard = zone_reply.payload["reply_markup"]
                keep_zone_token = next(
                    button["callback_data"]
                    for row in zone_keyboard["inline_keyboard"]
                    for button in row
                    if button["text"] == "Keep current"
                )
            await click(1_027, telegram_user_id, keep_zone_token)
            await click(1_028, telegram_user_id, keep_zone_token)
            async with sessions() as session:
                kept = await session.get(OnboardingRecord, owner_id)
                saved = await session.get(PreferencesRecord, owner_id)
                assert kept is not None and kept.step == "reminders"
                assert kept.draft["language"] == "en"
                assert kept.draft["home_zone"] == "Asia/Almaty"
                assert saved is not None and saved.version == 1
                assert saved.language == "ru" and saved.home_zone == "Asia/Almaty"

            # A profile with no retained onboarding row starts each preview at the
            # same version. The old confirm token must still fail after cancel/restart.
            preview_user_id = foreign_telegram_user_id + 1
            preview_owner_id = uuid4()
            async with sessions() as session, session.begin():
                session.add(
                    TelegramOwnerRecord(
                        bot_id=bot_id,
                        telegram_user_id=preview_user_id,
                        owner_id=preview_owner_id,
                    )
                )
                saved = await SqlAlchemyIdentityStore(session).save_preferences(
                    owner_id=preview_owner_id,
                    expected_version=None,
                    preferences=Preferences(language="en", home_zone="Asia/Almaty"),
                )
                assert saved is not None

            update_id = 1_100
            await message_as(update_id, preview_user_id, "/settings")
            first_draft_id = None
            for text in ("en", "keep", "keep", "keep"):
                update_id += 1
                await message_as(update_id, preview_user_id, text)
            async with sessions() as session:
                first_preview = await session.get(OnboardingRecord, preview_owner_id)
                assert first_preview is not None and first_preview.step == "confirm"
                first_draft_id = first_preview.draft_id
                first_version = first_preview.version
                first_reply = [
                    event
                    for event in await _owner_events(session, preview_owner_id)
                    if event.event_type == "telegram.reply.requested"
                ][-1]
                first_keyboard = first_reply.payload["reply_markup"]
                stale_confirm = next(
                    button["callback_data"]
                    for row in first_keyboard["inline_keyboard"]
                    for button in row
                    if button["text"] == "Confirm"
                )
            update_id += 1
            await message_as(update_id, preview_user_id, "/cancel")
            update_id += 1
            await message_as(update_id, preview_user_id, "/settings")
            for text in ("en", "keep", "keep", "keep"):
                update_id += 1
                await message_as(update_id, preview_user_id, text)
            async with sessions() as session:
                recreated = await session.get(OnboardingRecord, preview_owner_id)
                assert recreated is not None and recreated.step == "confirm"
                assert recreated.version == first_version
                assert recreated.draft_id != first_draft_id
            update_id += 1
            await click(update_id, preview_user_id, stale_confirm)
            async with sessions() as session:
                recreated = await session.get(OnboardingRecord, preview_owner_id)
                untouched = await session.get(PreferencesRecord, preview_owner_id)
                assert recreated is not None and recreated.step == "confirm"
                assert untouched is not None and untouched.version == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_lock_preferences_refreshes_cached_snapshot_and_holds_the_row_lock(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        try:
            async with sessions() as session, session.begin():
                inserted = await SqlAlchemyIdentityStore(session).save_preferences(
                    owner_id=owner_id,
                    expected_version=None,
                    preferences=Preferences(language="en", home_zone="Asia/Almaty"),
                )
                assert inserted is not None

            async with sessions() as lock_session:
                async with lock_session.begin():
                    store = SqlAlchemyIdentityStore(lock_session)
                    cached = await store.get_preferences(owner_id)
                    assert cached is not None and cached.version == 1
                    assert cached.home_zone == "Asia/Almaty"

                    async with sessions() as update_session, update_session.begin():
                        update_store = SqlAlchemyIdentityStore(update_session)
                        current = await update_store.get_preferences(owner_id)
                        assert current is not None
                        committed = await update_store.save_preferences(
                            owner_id=owner_id,
                            expected_version=current.version,
                            preferences=Preferences(
                                language=current.language,
                                home_zone="Asia/Aqtobe",
                                reminder_calendar_days=current.reminder_calendar_days,
                                reminder_elapsed_hours=current.reminder_elapsed_hours,
                                quiet_start=current.quiet_start,
                                quiet_end=current.quiet_end,
                                review_time=current.review_time,
                                version=current.version + 1,
                            ),
                        )
                        assert committed is not None

                    locked = await store.lock_preferences(owner_id)
                    assert locked is not None
                    assert locked.version == 2 and locked.home_zone == "Asia/Aqtobe"

                    update_started = asyncio.Event()
                    update_committed = asyncio.Event()

                    async def concurrent_update() -> None:
                        async with sessions() as writer, writer.begin():
                            writer_store = SqlAlchemyIdentityStore(writer)
                            writer_current = await writer_store.get_preferences(owner_id)
                            assert writer_current is not None
                            update_started.set()
                            result = await writer_store.save_preferences(
                                owner_id=owner_id,
                                expected_version=writer_current.version,
                                preferences=Preferences(
                                    language=writer_current.language,
                                    home_zone=writer_current.home_zone,
                                    reminder_calendar_days=writer_current.reminder_calendar_days,
                                    reminder_elapsed_hours=writer_current.reminder_elapsed_hours,
                                    quiet_start=writer_current.quiet_start,
                                    quiet_end=writer_current.quiet_end,
                                    review_time=writer_current.review_time.replace(hour=21),
                                    version=writer_current.version + 1,
                                ),
                            )
                            assert result is not None
                        update_committed.set()

                    writer_task = asyncio.create_task(concurrent_update())
                    await asyncio.wait_for(update_started.wait(), timeout=2)
                    await asyncio.sleep(0.15)
                    assert not update_committed.is_set()

                await asyncio.wait_for(update_committed.wait(), timeout=5)
                await writer_task

            async with sessions() as session:
                final = await SqlAlchemyIdentityStore(session).get_preferences(owner_id)
                assert final is not None and final.version == 3
                assert final.home_zone == "Asia/Aqtobe"
                assert final.review_time.hour == 21
        finally:
            await engine.dispose()

    run_local_async(scenario)
