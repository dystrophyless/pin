from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

import pin.adapters.telegram.application_worker as telegram_application_worker
from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.contexts.billing.api import SqlAlchemyAccessUsageStore
from pin.contexts.billing.infrastructure.models import (
    AccessPeriodRecord,
    TrialActivationRecord,
)
from pin.contexts.identity.application.contracts import ProcessingAuthorization
from pin.contexts.identity.application.onboarding import (
    answer_onboarding,
    begin_preferences_edit,
    confirm_preferences_edit,
)
from pin.contexts.identity.application.processing_consent import (
    PROCESSING_CONSENT_POLICY_VERSION,
    ProcessingConsentConflict,
    accept_processing_consent,
    authorize_processing_consent,
    get_processing_consent,
    invalidate_restored_processing_consent,
    revoke_processing_consent,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.deletion import purge_owner as purge_identity_owner
from pin.contexts.identity.infrastructure.models import (
    OnboardingRecord,
    PreferencesRecord,
    ProcessingConsentRecord,
)
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.contracts import EventEnvelope
from pin.platform.models import InboxUpdate, OutboxEvent

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


@pytest.mark.parametrize(
    ("language_code", "first_text", "expected_language"),
    [
        ("ru", "/start", "ru"),
        ("ru-RU", "hello", "ru"),
        ("en", "/start", "en"),
        ("kk", "hello", "en"),
        (None, "/start", "en"),
    ],
)
def test_first_private_message_creates_preferences_and_privacy_grant_starts_one_trial(
    onboarding_database: str,
    language_code: str | None,
    first_text: str,
    expected_language: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = None
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        try:

            async def send(
                update_id: int,
                text: str,
                *,
                language_code: str | None = "ru",
            ) -> None:
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
                                "from": {
                                    "id": telegram_user_id,
                                    "is_bot": False,
                                    **({"language_code": language_code} if language_code else {}),
                                },
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

            async def click(update_id: int, callback_data: str) -> None:
                now = datetime.now(UTC)
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload={
                            "update_id": update_id,
                            "callback_query": {
                                "id": f"onboarding-consent-{uuid4().hex}",
                                "from": {"id": telegram_user_id, "is_bot": False},
                                "message": {
                                    "message_id": update_id,
                                    "chat": {"id": telegram_user_id, "type": "private"},
                                },
                                "data": callback_data,
                            },
                        },
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                claim = await claim_application_job(sessions, worker_id="onboarding-test")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)

            await send(1, first_text, language_code=language_code)
            async with sessions() as session:
                assert owner_id is not None
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None
                assert (profile.language, profile.home_zone, profile.version) == (
                    expected_language,
                    "UTC",
                    1,
                )
                assert profile.reminder_calendar_days == profile.reminder_elapsed_hours == 1
                assert profile.quiet_start.isoformat() == "22:00:00"
                assert profile.quiet_end.isoformat() == "08:00:00"
                assert profile.review_time.isoformat() == "20:00:00"
                assert await session.get(OnboardingRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None

            # Returning /start keeps the inferred profile and leaves consent and trial untouched.
            await send(2, "/start", language_code="en")
            await send(3, "/privacy", language_code="en")
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                consent = await session.get(ProcessingConsentRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
                assert (
                    profile is not None
                    and profile.language == expected_language
                    and profile.version == 1
                )
                assert consent is None and trial is None
                privacy_reply = await session.scalar(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.owner_id == owner_id,
                        OutboxEvent.event_type == "telegram.reply.requested",
                    )
                    .order_by(OutboxEvent.created_at.desc())
                    .limit(1)
                )
                assert privacy_reply is not None
                buttons = privacy_reply.payload["payload"]["reply_markup"]["inline_keyboard"]
                assert len(buttons) == 1 and len(buttons[0]) == 1
                grant = buttons[0][0]["callback_data"]
            await click(4, grant)

            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                consent = await session.get(ProcessingConsentRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
                assert profile is not None and profile.version == 1
                assert consent is not None and consent.state == "granted"
                assert trial is not None and trial.telegram_user_id == telegram_user_id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 1
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

            # Repeating /start never resets Preferences or starts another trial.
            await send(5, "/start", language_code="en")
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert (
                    profile is not None
                    and profile.version == 1
                    and profile.language == expected_language
                )
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
            async with sessions() as session:
                baseline = await session.get(PreferencesRecord, owner_id)
                assert baseline is not None
                baseline_version = baseline.version
                baseline_zone = baseline.home_zone
                assert await session.get(TrialActivationRecord, owner_id) is None
            # Settings edits remain available before AI consent; cancelling preserves
            # the automatically created profile and does not start its trial.
            await send(101, "/settings")
            await send(102, "keep")
            await send(103, "Asia/Aqtobe")
            await send(104, "/cancel")
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
                    == 0
                )

            # A confirmed home-zone edit publishes a versioned downstream handoff.
            await send(113, "/settings")
            await send(114, "keep")
            await send(115, "Asia/Aqtobe")
            await send(116, "keep")
            await send(117, "keep")
            review = await send(118, "confirm")
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
                127, callback=callback_for(review, "Keep local schedules")
            )
            async with sessions() as session:
                still_pending = await session.get(PreferencesRecord, owner_id)
                assert still_pending is not None
                assert still_pending.home_zone == baseline_zone
                assert still_pending.version == baseline_version
            await send(128, callback=callback_for(preview, "Confirm zone change"))
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
                    "previous_home_zone": baseline_zone,
                    "requested_home_zone": "Asia/Aqtobe",
                    "preferences_version": profile.version,
                }
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TrialActivationRecord)
                        .where(TrialActivationRecord.owner_id == owner_id)
                    )
                    == 0
                )

            # A newer version committed elsewhere wins; this preview cannot overwrite it.
            await send(119, "/settings")
            await send(120, "keep")
            await send(121, "keep")
            await send(122, "keep")
            await send(123, "keep")
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
            await send(124, "confirm")
            async with sessions() as session:
                current = await session.get(PreferencesRecord, owner_id)
                assert current is not None
                assert current.version == baseline_version + 2
                assert current.review_time.hour == 19
                assert await session.get(OnboardingRecord, owner_id) is None
            await send(125, "/settings")
            async with sessions() as session:
                refreshed = await session.get(OnboardingRecord, owner_id)
                assert refreshed is not None
                assert refreshed.base_profile_version == baseline_version + 2
                assert refreshed.draft["review_time"] == "19:00:00"
            await send(126, "/cancel")
            async with sessions() as session:
                assert await session.get(OnboardingRecord, owner_id) is None
            plan_cancel = await send(129, "/cancel")
            assert "There are no open tasks for this action." in str(plan_cancel["plain_text"])
            assert plan_cancel["handling"] == "handled"
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_private_timezone_widget_previews_and_confirms_device_zone(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        bot_id = 987654 + uuid4().int % 1_000_000
        user_id = 1 + uuid4().int % (2**52 - 2)
        now = datetime.now(UTC)
        update_id = 0

        async def dispatch(kind: str, payload: dict[str, object]) -> dict[str, object]:
            nonlocal update_id
            update_id += 1
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload={"update_id": update_id, kind: payload},
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
            claim = await claim_application_job(sessions, worker_id="timezone-widget")
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

        def message(text: str) -> dict[str, object]:
            return {
                "message_id": update_id + 1,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "language_code": "en"},
                "text": text,
            }

        def query(token: str, zone: str, actor_id: int = user_id) -> dict[str, object]:
            return {
                "id": f"zone-widget-{update_id + 1}",
                "from": {"id": actor_id, "is_bot": False, "first_name": "Pin Test"},
                "chat": {"id": actor_id, "type": "private"},
                "message_id": 1,
                "chat_instance": "123456",
                "type": {"type": "data"},
                "data": f"{token}|{zone}",
            }

        def token_from(reply: dict[str, object]) -> str:
            html = reply["widget_html"]
            assert isinstance(html, str)
            match = re.search(r'const token = ("[^"]+");', html)
            assert match is not None
            return cast(str, json.loads(match[1]))

        async def confirm_zone(preview: dict[str, object]) -> None:
            markup = cast(dict[str, list[list[dict[str, str]]]], preview["reply_markup"])
            confirmation = next(
                button["callback_data"]
                for row in markup["inline_keyboard"]
                for button in row
                if button["text"] == "Confirm zone change"
            )
            await dispatch(
                "callback_query",
                {
                    "id": f"zone-confirm-{update_id + 1}",
                    "from": {"id": user_id, "is_bot": False},
                    "message": {"chat": {"id": user_id, "type": "private"}},
                    "data": confirmation,
                },
            )

        try:
            token = token_from(await dispatch("message", message("/start")))
            denied = await dispatch("widget_query", query(token, "Europe/Paris", user_id + 1))
            assert "Could not accept" in str(denied["plain_text"])
            invalid = await dispatch("widget_query", query(token, "Not/AZone"))
            assert "Could not accept" in str(invalid["plain_text"])
            preview = await dispatch("widget_query", query(token, "Asia/Almaty"))
            assert "Asia/Almaty" in str(preview["plain_text"])

            async with sessions() as session:
                owner_id = await session.scalar(
                    select(TelegramOwnerRecord.owner_id).where(
                        TelegramOwnerRecord.bot_id == bot_id,
                        TelegramOwnerRecord.telegram_user_id == user_id,
                    )
                )
                assert owner_id is not None
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.home_zone == "UTC"

            await confirm_zone(preview)
            replay = await dispatch("widget_query", query(token, "Europe/Paris"))
            assert "Could not accept" in str(replay["plain_text"])
            entry = await dispatch("message", message("/timezone"))
            assert "widget_html" in entry
            assert "/timezone London" in str(entry["plain_text"])
            assert "widget_html" not in await dispatch("message", message("/start"))

            await dispatch("message", message("/settings"))
            settings_token = token_from(await dispatch("message", message("keep")))
            reminders = await dispatch("widget_query", query(settings_token, "Europe/Paris"))
            assert "Reminder suggestions" in str(reminders["plain_text"])

            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.home_zone == "Asia/Almaty"
                assert profile.version == 2 and profile.reminder_calendar_days == 1
                assert await session.get(TrialActivationRecord, owner_id) is None
                draft = await session.get(OnboardingRecord, owner_id)
                assert draft is not None and draft.step == "reminders"
                assert draft.draft["home_zone"] == "Europe/Paris"

            active_edit = await dispatch("message", message("/timezone New York"))
            assert "Finish the current edit" in str(active_edit["plain_text"])
            await dispatch("message", message("/cancel"))
            invalid_city = await dispatch("message", message("/timezone Unknown City"))
            assert "Could not identify" in str(invalid_city["plain_text"])
            same_zone = await dispatch("message", message("/timezone Almaty"))
            assert "already Asia/Almaty" in str(same_zone["plain_text"])

            manual_preview = await dispatch("message", message("/timezone New York"))
            assert "America/New_York" in str(manual_preview["plain_text"])
            assert "widget_html" not in manual_preview
            await confirm_zone(manual_preview)
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.home_zone == "America/New_York"
                assert profile.version == 3 and profile.reminder_calendar_days == 1
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


def test_privacy_grant_and_trial_roll_back_in_the_same_unit_of_work(
    onboarding_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = None
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        try:

            async def dispatch(update_id: int, payload: dict[str, object]) -> dict[str, object]:
                nonlocal owner_id
                now = datetime.now(UTC)
                payload["update_id"] = update_id
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=payload,
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                    owner_id = await session.scalar(
                        select(TelegramOwnerRecord.owner_id).where(
                            TelegramOwnerRecord.bot_id == bot_id,
                            TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                        )
                    )
                    assert owner_id is not None
                claim = await claim_application_job(sessions, worker_id="privacy-grant-rollback")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)
                async with sessions() as session:
                    event = await session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.owner_id == owner_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.causation_id == claim.job_id,
                        )
                    )
                    assert event is not None
                    return cast(dict[str, object], event.payload["payload"])

            async def message(update_id: int, text: str) -> dict[str, object]:
                return await dispatch(
                    update_id,
                    {
                        "message": {
                            "message_id": update_id,
                            "date": int(datetime.now(UTC).timestamp()),
                            "chat": {"id": telegram_user_id, "type": "private"},
                            "from": {
                                "id": telegram_user_id,
                                "is_bot": False,
                                "language_code": "en",
                            },
                            "text": text,
                        }
                    },
                )

            await message(1, "/start")
            privacy = await message(2, "/privacy")
            privacy_buttons = privacy["reply_markup"]["inline_keyboard"]
            assert len(privacy_buttons) == 1 and len(privacy_buttons[0]) == 1
            grant = privacy_buttons[0][0]["callback_data"]
            original_activate_trial = SqlAlchemyAccessUsageStore.activate_trial

            async def fail_after_trial_write(self, command):
                await original_activate_trial(self, command)
                raise RuntimeError("simulate transaction failure after Billing writes")

            monkeypatch.setattr(
                SqlAlchemyAccessUsageStore, "activate_trial", fail_after_trial_write
            )
            callback = {
                "callback_query": {
                    "id": "privacy-grant-rollback",
                    "from": {
                        "id": telegram_user_id,
                        "is_bot": False,
                        "language_code": "en",
                    },
                    "message": {
                        "message_id": 3,
                        "chat": {"id": telegram_user_id, "type": "private"},
                    },
                    "data": grant,
                }
            }
            with pytest.raises(RuntimeError, match="simulate transaction failure"):
                await dispatch(3, callback)

            async with sessions() as session:
                assert owner_id is not None
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.language == "en" and profile.version == 1
                assert await session.get(ProcessingConsentRecord, owner_id) is None
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

def test_invalid_preference_answers_keep_the_step_and_city_zone_is_accepted(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                saved = await store.save_preferences(
                    owner_id=owner_id,
                    expected_version=None,
                    preferences=Preferences(language="en", home_zone="UTC"),
                )
                assert saved is not None and saved.version == 1
                assert (await begin_preferences_edit(store, owner_id=owner_id)).step == "language"
                assert (
                    await answer_onboarding(store, owner_id=owner_id, text="keep")
                ).step == "home_zone"
                invalid_zone = await answer_onboarding(
                    store, owner_id=owner_id, text="Mars/Olympus"
                )
                assert invalid_zone.state == "invalid_input" and invalid_zone.step == "home_zone"
                assert (
                    await answer_onboarding(store, owner_id=owner_id, text="Astana")
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
                ready = await answer_onboarding(
                    store, owner_id=owner_id, text="suggested"
                )
                assert ready.step == "confirm"
                unchanged = await store.get_preferences(owner_id)
                assert unchanged is not None
                assert unchanged.version == 1 and unchanged.home_zone == "UTC"
                result = await confirm_preferences_edit(
                    store, owner_id=owner_id, now=datetime.now(UTC)
                )
                assert result.state == "confirmed"
                assert result.preferences is not None
                assert result.preferences.version == 2
                assert result.preferences.home_zone == "Asia/Almaty"
                assert await store.get_onboarding(owner_id) is not None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_simultaneous_preference_confirmations_advance_one_version(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                saved = await store.save_preferences(
                    owner_id=owner_id,
                    expected_version=None,
                    preferences=Preferences(language="en", home_zone="UTC"),
                )
                assert saved is not None
                await begin_preferences_edit(store, owner_id=owner_id)
                for text in ("keep", "Astana", "suggested", "suggested"):
                    await answer_onboarding(store, owner_id=owner_id, text=text)

            async def confirm_once():
                async with sessions() as session, session.begin():
                    return await confirm_preferences_edit(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        now=datetime.now(UTC),
                    )

            results = await asyncio.gather(confirm_once(), confirm_once())
            assert sum(result.state == "confirmed" for result in results) == 1
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.version == 2
                assert profile.home_zone == "Asia/Almaty"
                assert await session.get(TrialActivationRecord, owner_id) is None
        finally:
            await engine.dispose()

    run_local_async(scenario)

def test_legacy_initial_draft_and_callback_are_ignored_after_profile_creation(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        old_token = "old-initial-setup-token"
        try:
            async with sessions() as session, session.begin():
                session.add(
                    TelegramOwnerRecord(
                        bot_id=bot_id,
                        telegram_user_id=telegram_user_id,
                        owner_id=owner_id,
                    )
                )
                await session.flush()
                store = SqlAlchemyIdentityStore(session)
                draft = await store.begin_onboarding(
                    owner_id=owner_id,
                    mode="initial",
                    initial_values={},
                    base_profile_version=None,
                )
                session.add(
                    TelegramCallbackTokenRecord(
                        token_hash=hashlib.sha256(old_token.encode("ascii")).hexdigest(),
                        owner_id=owner_id,
                        action="answer",
                        target_id=f"{draft.draft_id}|language|ru",
                        target_version=draft.version,
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )

            async def dispatch(update_id: int, payload: dict[str, object]) -> None:
                now = datetime.now(UTC)
                payload["update_id"] = update_id
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=payload,
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert receipt is not None and receipt.accepted
                claim = await claim_application_job(sessions, worker_id="legacy-setup-callback")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)

            await dispatch(
                1,
                {
                    "message": {
                        "message_id": 1,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": telegram_user_id, "type": "private"},
                        "from": {
                            "id": telegram_user_id,
                            "is_bot": False,
                            "language_code": "ru-RU",
                        },
                        "text": "/start",
                    }
                },
            )
            await dispatch(
                2,
                {
                    "callback_query": {
                        "id": "old-initial-setup-callback",
                        "from": {"id": telegram_user_id, "is_bot": False},
                        "message": {
                            "message_id": 1,
                            "chat": {"id": telegram_user_id, "type": "private"},
                        },
                        "data": old_token,
                    }
                },
            )
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.language == "ru" and profile.version == 1
                assert await session.get(OnboardingRecord, owner_id) is None
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_payment_receipt_does_not_create_initial_preferences(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        telegram_user_id = uuid4().int % 2**52 or 1
        bot_id = 987654 + uuid4().int % 1_000_000
        try:
            now = datetime.now(UTC)
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload={
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "date": int(now.timestamp()),
                            "chat": {"id": telegram_user_id, "type": "private"},
                            "from": {"id": telegram_user_id, "is_bot": False},
                            "successful_payment": {
                                "invoice_payload": "unmatched-invoice",
                                "telegram_payment_charge_id": "unmatched-charge",
                                "provider_payment_charge_id": "provider-charge",
                                "currency": "XTR",
                                "total_amount": 100,
                                "is_recurring": False,
                                "is_first_recurring": False,
                            },
                        },
                    },
                    owner_resolver=PostgresTelegramOwnerResolver(),
                )
                assert receipt is not None and receipt.accepted
                owner_id = await session.scalar(
                    select(TelegramOwnerRecord.owner_id).where(
                        TelegramOwnerRecord.bot_id == bot_id,
                        TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                    )
                )
                assert owner_id is not None
            claim = await claim_application_job(sessions, worker_id="payment-receipt-first")
            assert claim is not None
            await process_application_job(sessions, claim, now=now)
            async with sessions() as session:
                assert await session.get(PreferencesRecord, owner_id) is None
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_settings_callbacks_are_owner_bound_versioned_and_single_use(
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

            async def dispatch(update_id: int, update: dict[str, object]) -> dict[str, object]:
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
                claim = await claim_application_job(sessions, worker_id="settings-callback-test")
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

            async def message(update_id: int, actor_id: int, text: str) -> dict[str, object]:
                return await dispatch(
                    update_id,
                    {
                        "message": {
                            "message_id": update_id,
                            "chat": {"id": actor_id, "type": "private"},
                            "from": {
                                "id": actor_id,
                                "is_bot": False,
                                "language_code": "ru",
                            },
                            "text": text,
                        }
                    },
                )

            async def click(update_id: int, actor_id: int, token: str) -> dict[str, object]:
                return await dispatch(
                    update_id,
                    {
                        "callback_query": {
                            "id": f"settings-callback-{update_id}",
                            "from": {"id": actor_id, "is_bot": False},
                            "message": {
                                "message_id": update_id,
                                "chat": {"id": actor_id, "type": "private"},
                            },
                            "data": token,
                        }
                    },
                )

            start_reply = await message(1_000, telegram_user_id, "/start")
            assert owner_id is not None
            buttons = start_reply["reply_markup"]["inline_keyboard"]
            assert len(buttons) == 1 and len(buttons[0]) == 1
            settings_token = buttons[0][0]["callback_data"]
            await click(1_001, foreign_telegram_user_id, settings_token)
            settings_reply = await click(1_002, telegram_user_id, settings_token)
            await click(1_003, telegram_user_id, settings_token)  # duplicate use
            await click(1_004, telegram_user_id, "forged-token")

            language_buttons = settings_reply["reply_markup"]["inline_keyboard"]
            flattened_buttons = [button for row in language_buttons for button in row]
            assert len(flattened_buttons) == 4
            keep_token = flattened_buttons[2]["callback_data"]
            home_zone_reply = await click(1_005, telegram_user_id, keep_token)
            await click(1_006, telegram_user_id, keep_token)  # stale step
            await message(1_007, telegram_user_id, "Almaty")

            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                draft = await session.get(OnboardingRecord, owner_id)
                assert profile is not None and profile.language == "ru" and profile.version == 1
                assert draft is not None and draft.mode == "preferences"
                assert draft.step == "reminders"
                assert draft.draft["home_zone"] == "Asia/Almaty"
                assert await session.get(TrialActivationRecord, owner_id) is None
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                replies = [
                    event
                    for event in await _owner_events(session, owner_id)
                    if event.event_type == "telegram.reply.requested"
                ]
                assert len(replies) == 7
                assert home_zone_reply["plain_text"]
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


async def _add_consent_inbox(
    session, *, owner_id, telegram_user_id: int, bot_id: int = 7654321
):
    inbox_id = uuid4()
    session.add(
        InboxUpdate(
            id=inbox_id,
            bot_id=bot_id,
            update_id=uuid4().int % 2**52,
            owner_id=owner_id,
            update_type="message",
            payload={
                "message": {
                    "from": {"id": telegram_user_id, "is_bot": False},
                    "chat": {"id": telegram_user_id, "type": "private"},
                }
            },
        )
    )
    await session.flush()
    return inbox_id


def test_processing_consent_persists_revisions_regrant_invalidation_and_purge(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = 4_503_599_627_370_000
        now = datetime.now(UTC)
        try:
            async with sessions() as session:
                missing_store = SqlAlchemyIdentityStore(session)
                missing = await get_processing_consent(missing_store, owner_id=owner_id)
                assert missing.state == "required" and missing.revision == 0
                assert not await authorize_processing_consent(
                    missing_store,
                    owner_id=owner_id,
                    authorization=ProcessingAuthorization(
                        owner_id=owner_id,
                        grant_id=uuid4(),
                        policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    ),
                )

            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                update_id = await _add_consent_inbox(
                    session, owner_id=owner_id, telegram_user_id=telegram_user_id
                )
                first = await accept_processing_consent(
                    store,
                    owner_id=owner_id,
                    expected_revision=0,
                    policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    language="en",
                    accepted_at=now,
                    telegram_user_id=telegram_user_id,
                    inbox_update_id=update_id,
                )
                assert first.state == "granted" and first.revision == 1
                assert first.accepted_telegram_user_id == telegram_user_id
                assert first.accepted_update_id == update_id
                first_grant_id = first.grant_id
                assert first_grant_id is not None

            current_auth = ProcessingAuthorization(
                owner_id=owner_id,
                grant_id=first_grant_id,
                policy_version=PROCESSING_CONSENT_POLICY_VERSION,
            )
            async with sessions() as session:
                store = SqlAlchemyIdentityStore(session)
                assert await authorize_processing_consent(
                    store, owner_id=owner_id, authorization=current_auth
                )
                assert not await authorize_processing_consent(
                    store, owner_id=uuid4(), authorization=current_auth
                )
                assert not await authorize_processing_consent(
                    store,
                    owner_id=owner_id,
                    authorization=ProcessingAuthorization(
                        owner_id=owner_id,
                        grant_id=first_grant_id,
                        policy_version="stale-policy",
                    ),
                )
            with pytest.raises(ProcessingConsentConflict, match="policy is stale"):
                async with sessions() as session, session.begin():
                    update_id = await _add_consent_inbox(
                        session, owner_id=owner_id, telegram_user_id=telegram_user_id
                    )
                    await accept_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        expected_revision=1,
                        policy_version="stale-policy",
                        language="en",
                        accepted_at=now,
                        telegram_user_id=telegram_user_id,
                        inbox_update_id=update_id,
                    )

            async with sessions() as session, session.begin():
                revoked = await revoke_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    expected_revision=1,
                    expected_grant_id=first_grant_id,
                    expected_policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    revoked_at=datetime.now(UTC),
                )
                assert revoked.state == "revoked" and revoked.revision == 2
            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                update_id = await _add_consent_inbox(
                    session, owner_id=owner_id, telegram_user_id=telegram_user_id
                )
                second = await accept_processing_consent(
                    store,
                    owner_id=owner_id,
                    expected_revision=2,
                    policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    language="ru",
                    accepted_at=datetime.now(UTC),
                    telegram_user_id=telegram_user_id,
                    inbox_update_id=update_id,
                )
                assert second.state == "granted" and second.revision == 3
                assert second.grant_id is not None and second.grant_id != first_grant_id
                assert not await authorize_processing_consent(
                    store, owner_id=owner_id, authorization=current_auth
                )
                assert await invalidate_restored_processing_consent(store, owner_id=owner_id)
                assert not await invalidate_restored_processing_consent(store, owner_id=owner_id)
                required = await get_processing_consent(store, owner_id=owner_id)
                assert required.state == "required" and required.revision == 4
                assert required.grant_id is None and required.policy_version is None

            async with sessions() as session, session.begin():
                deleted = await purge_identity_owner(session, owner_id)
                assert deleted >= 1
            async with sessions() as session:
                purged = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                assert purged.state == "required" and purged.revision == 0
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_missing_row_grant_and_revoke_race_is_serialized(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = 4_503_599_627_370_000
        now = datetime.now(UTC)
        try:
            async with sessions() as session, session.begin():
                update_id = await _add_consent_inbox(
                    session, owner_id=owner_id, telegram_user_id=telegram_user_id
                )

            barrier = asyncio.Barrier(2)

            async def grant():
                async with sessions() as session, session.begin():
                    await barrier.wait()
                    return await accept_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        expected_revision=0,
                        policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                        language="en",
                        accepted_at=now,
                        telegram_user_id=telegram_user_id,
                        inbox_update_id=update_id,
                    )

            async def revoke_missing():
                async with sessions() as session, session.begin():
                    await barrier.wait()
                    return await revoke_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        expected_revision=0,
                        expected_grant_id=uuid4(),
                        expected_policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                        revoked_at=datetime.now(UTC),
                    )

            grant_task = asyncio.create_task(grant())
            revoke_task = asyncio.create_task(revoke_missing())
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(grant_task, revoke_task, return_exceptions=True),
                    timeout=5,
                )
            finally:
                for task in (grant_task, revoke_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(grant_task, revoke_task, return_exceptions=True)
            assert sum(not isinstance(result, BaseException) for result in results) == 1
            assert sum(isinstance(result, ProcessingConsentConflict) for result in results) == 1
            async with sessions() as session:
                current = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                assert current.state == "granted" and current.revision == 1
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_authorization_and_withdrawal_hold_the_owner_fence_through_commit(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        telegram_user_id = 4_503_599_627_370_000
        tasks: list[asyncio.Task[object]] = []
        release_authorization = asyncio.Event()
        release_withdrawal = asyncio.Event()

        async def wait_until_blocked(blocked_pid: int, blocker_pid: int) -> None:
            async def observe() -> None:
                while True:
                    async with sessions() as monitor:
                        blockers = await monitor.scalar(
                            text("SELECT pg_blocking_pids(:blocked_pid)"),
                            {"blocked_pid": blocked_pid},
                        )
                    if blocker_pid in (blockers or []):
                        return
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(observe(), timeout=5)

        try:
            async with sessions() as session, session.begin():
                inbox_id = await _add_consent_inbox(
                    session, owner_id=owner_id, telegram_user_id=telegram_user_id
                )
                granted = await accept_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    expected_revision=0,
                    policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    language="en",
                    accepted_at=datetime.now(UTC),
                    telegram_user_id=telegram_user_id,
                    inbox_update_id=inbox_id,
                )
            granted_id = granted.grant_id
            assert granted_id is not None
            authorization = ProcessingAuthorization(
                owner_id=owner_id,
                grant_id=granted_id,
                policy_version=PROCESSING_CONSENT_POLICY_VERSION,
            )

            authorized = asyncio.Event()
            revoke_started = asyncio.Event()
            authorization_backend_pid: list[int] = []
            revoke_backend_pid: list[int] = []

            async def hold_authorization() -> None:
                async with sessions() as session, session.begin():
                    assert await authorize_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        authorization=authorization,
                    )
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    authorization_backend_pid.append(pid)
                    authorized.set()
                    await release_authorization.wait()

            async def withdraw_after_authorization() -> None:
                await authorized.wait()
                async with sessions() as session, session.begin():
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    revoke_backend_pid.append(pid)
                    revoke_started.set()
                    await revoke_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        expected_revision=1,
                        expected_grant_id=granted_id,
                        expected_policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                        revoked_at=datetime.now(UTC),
                    )

            authorization_task = asyncio.create_task(hold_authorization())
            tasks.append(authorization_task)
            await asyncio.wait_for(authorized.wait(), timeout=5)
            withdrawal_task = asyncio.create_task(withdraw_after_authorization())
            tasks.append(withdrawal_task)
            await asyncio.wait_for(revoke_started.wait(), timeout=5)
            await wait_until_blocked(
                revoke_backend_pid[0], authorization_backend_pid[0]
            )
            release_authorization.set()
            await asyncio.wait_for(asyncio.gather(authorization_task, withdrawal_task), timeout=5)
            async with sessions() as session:
                revoked = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                assert revoked.state == "revoked" and revoked.revision == 2

            async with sessions() as session, session.begin():
                inbox_id = await _add_consent_inbox(
                    session, owner_id=owner_id, telegram_user_id=telegram_user_id
                )
                regranted = await accept_processing_consent(
                    SqlAlchemyIdentityStore(session),
                    owner_id=owner_id,
                    expected_revision=2,
                    policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                    language="en",
                    accepted_at=datetime.now(UTC),
                    telegram_user_id=telegram_user_id,
                    inbox_update_id=inbox_id,
                )
            regranted_id = regranted.grant_id
            assert regranted_id is not None and regranted_id != granted_id
            fresh_authorization = ProcessingAuthorization(
                owner_id=owner_id,
                grant_id=regranted_id,
                policy_version=PROCESSING_CONSENT_POLICY_VERSION,
            )
            withdrawal_held = asyncio.Event()
            authorization_started = asyncio.Event()
            withdrawal_backend_pid: list[int] = []
            authorization_backend_pid = []
            authorization_result: list[bool] = []

            async def hold_withdrawal() -> None:
                async with sessions() as session, session.begin():
                    await revoke_processing_consent(
                        SqlAlchemyIdentityStore(session),
                        owner_id=owner_id,
                        expected_revision=3,
                        expected_grant_id=regranted_id,
                        expected_policy_version=PROCESSING_CONSENT_POLICY_VERSION,
                        revoked_at=datetime.now(UTC),
                    )
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    withdrawal_backend_pid.append(pid)
                    withdrawal_held.set()
                    await release_withdrawal.wait()

            async def authorize_after_withdrawal() -> None:
                await withdrawal_held.wait()
                async with sessions() as session, session.begin():
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    authorization_backend_pid.append(pid)
                    authorization_started.set()
                    authorization_result.append(
                        await authorize_processing_consent(
                            SqlAlchemyIdentityStore(session),
                            owner_id=owner_id,
                            authorization=fresh_authorization,
                        )
                    )

            withdrawal_task = asyncio.create_task(hold_withdrawal())
            tasks.append(withdrawal_task)
            await asyncio.wait_for(withdrawal_held.wait(), timeout=5)
            authorization_task = asyncio.create_task(authorize_after_withdrawal())
            tasks.append(authorization_task)
            await asyncio.wait_for(authorization_started.wait(), timeout=5)
            await wait_until_blocked(
                authorization_backend_pid[0], withdrawal_backend_pid[0]
            )
            release_withdrawal.set()
            await asyncio.wait_for(asyncio.gather(withdrawal_task, authorization_task), timeout=5)
            assert authorization_result == [False]
        finally:
            release_authorization.set()
            release_withdrawal.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    run_local_async(scenario)


def test_migration_does_not_backfill_existing_profiles(
    onboarding_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        try:
            command.downgrade(
                _alembic_config(onboarding_database), "platform_recovery_gate_0001"
            )
            async with sessions() as session, session.begin():
                saved = await SqlAlchemyIdentityStore(session).save_preferences(
                    owner_id=owner_id,
                    expected_version=None,
                    preferences=Preferences(language="en", home_zone="Asia/Almaty"),
                )
                assert saved is not None
            command.upgrade(_alembic_config(onboarding_database), "head")
            async with sessions() as session:
                assert await session.get(PreferencesRecord, owner_id) is not None
                assert await session.get(ProcessingConsentRecord, owner_id) is None
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_privacy_grant_is_explicit_owner_bound_and_policy_versioned(
    onboarding_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_engine(onboarding_database)
        sessions = create_session_factory(engine)
        owner_id = None
        telegram_user_id = 4_503_599_627_370_000
        bot_id = 8765432 + uuid4().int % 100_000
        try:

            async def dispatch(update_id: int, update: dict[str, object]) -> dict[str, object]:
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
                    owner_id = await session.scalar(
                        select(TelegramOwnerRecord.owner_id).where(
                            TelegramOwnerRecord.bot_id == bot_id,
                            TelegramOwnerRecord.telegram_user_id == telegram_user_id,
                        )
                    )
                    assert owner_id is not None
                claim = await claim_application_job(sessions, worker_id="privacy-grant-acceptance")
                assert claim is not None
                await process_application_job(sessions, claim, now=now)
                async with sessions() as session:
                    event = await session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.owner_id == claim.owner_id,
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.causation_id == claim.job_id,
                        )
                    )
                    assert event is not None
                    return cast(dict[str, object], event.payload["payload"])

            async def message(update_id: int, text: str) -> dict[str, object]:
                return await dispatch(
                    update_id,
                    {
                        "message": {
                            "message_id": update_id,
                            "date": int(datetime.now(UTC).timestamp()),
                            "chat": {"id": telegram_user_id, "type": "private"},
                            "from": {
                                "id": telegram_user_id,
                                "is_bot": False,
                                "language_code": "en",
                            },
                            "text": text,
                        }
                    },
                )

            async def click_as(
                update_id: int, actor_id: int, token: str
            ) -> dict[str, object]:
                return await dispatch(
                    update_id,
                    {
                        "callback_query": {
                            "id": f"privacy-grant-{update_id}",
                            "from": {"id": actor_id, "is_bot": False},
                            "message": {
                                "message_id": update_id,
                                "chat": {"id": actor_id, "type": "private"},
                            },
                            "data": token,
                        }
                    },
                )

            privacy = await message(1, "/privacy")
            assert "OpenRouter" in privacy["plain_text"]
            assert len(privacy["reply_markup"]["inline_keyboard"]) == 1
            original_token = privacy["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                assert profile is not None and profile.language == "en" and profile.version == 1
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None

            await click_as(2, telegram_user_id + 1, original_token)
            async with sessions() as session:
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None

            with monkeypatch.context() as stale_policy:
                stale_policy.setattr(
                    telegram_application_worker,
                    "PROCESSING_CONSENT_POLICY_VERSION",
                    "processing-consent-v2",
                )
                await click_as(3, telegram_user_id, original_token)
            async with sessions() as session:
                assert await session.get(ProcessingConsentRecord, owner_id) is None
                assert await session.get(TrialActivationRecord, owner_id) is None

            refreshed_privacy = await message(4, "/privacy")
            current_token = refreshed_privacy["reply_markup"]["inline_keyboard"][0][0][
                "callback_data"
            ]
            await click_as(5, telegram_user_id, current_token)
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                consent = await session.get(ProcessingConsentRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
                assert profile is not None and profile.version == 1
                assert consent is not None and consent.state == "granted"
                assert trial is not None and trial.telegram_user_id == telegram_user_id
        finally:
            await engine.dispose()

    run_local_async(scenario)
