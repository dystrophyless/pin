from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import claim_application_job, process_application_job
from pin.adapters.telegram.delivery_worker import process_delivery_iteration
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    TrialActivationRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.contexts.identity.infrastructure.models import (
    OnboardingRecord,
    PreferencesRecord,
    ProcessingConsentRecord,
)
from pin.contexts.notifications.application.delivery import TelegramReplySend
from pin.contexts.notifications.infrastructure.models import DeliveryAttemptRecord
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.models import InboxUpdate, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "PIN_TEST_CONSENT_DATABASE_URL"
DATABASE_PREFIX = "pin_consent_guard_"
BOT_BASE = 7_100_000_000
USER_BASE = 6_100_000_000


def _database_url() -> str:
    value = os.getenv(DATABASE_ENV)
    if not value:
        pytest.skip(f"set {DATABASE_ENV} to the assigned disposable consent database")
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost"}
        or not url.database
        or not url.database.startswith(DATABASE_PREFIX)
    ):
        pytest.fail("consent integration URL does not match the assigned disposable fixture")
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))
    command.upgrade(config, "head")
    return value


@pytest.fixture(scope="module")
def consent_integration_database() -> Iterator[str]:
    yield _database_url()


class _CountedInterpreter:
    def __init__(self, language: str) -> None:
        self.language = language
        self.calls = 0

    async def complete(self, _payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if self.calls > 2:
            raise AssertionError("the integrated scenario exceeded its two counted fake calls")
        reply = (
            "Your plan is ready for a fresh request."
            if self.language == "en"
            else "План готов к новому запросу."
        )
        return {
            "id": f"consent-integration-{uuid4().hex}",
            "model": ROUTE,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"kind": "reply", "language": self.language, "text": reply}
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
                "cost": "0.000001",
            },
        }


class _FakeTelegram:
    def __init__(self) -> None:
        self.replies: list[tuple[str, TelegramReplySend]] = []

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        destination = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id
            )
        )
        return int(destination) if destination is not None else None

    async def send_reply(self, message: TelegramReplySend) -> str:
        attempt_id = f"consent-fake-reply-{len(self.replies) + 1:03d}"
        self.replies.append((attempt_id, message))
        return attempt_id


def _bounded_text_repr(value: str, *, limit: int = 240) -> str:
    rendered = repr(value[:limit])
    return rendered if len(value) <= limit else f"{rendered}...<{len(value)} chars total>"


async def _submit(
    sessions: async_sessionmaker[AsyncSession],
    delivery: _FakeTelegram,
    *,
    bot_id: int,
    telegram_user_id: int,
    update: dict[str, object],
    at: datetime,
    assistant: BoundedPlanningAssistant,
) -> tuple[UUID, UUID, dict[str, object]]:
    update_id = uuid4().int % 9_000_000_000_000_000
    update["update_id"] = update_id
    async with sessions() as session, session.begin():
        receipt = await persist_update(
            session,
            bot_id=bot_id,
            payload=update,
            owner_resolver=PostgresTelegramOwnerResolver(),
        )
        assert receipt is not None and receipt.accepted
        inbox = await session.scalar(
            select(InboxUpdate).where(
                InboxUpdate.bot_id == bot_id,
                InboxUpdate.update_id == update_id,
            )
        )
        assert inbox is not None and inbox.owner_id is not None
        owner_id = inbox.owner_id
        inbox_id = inbox.id

    claim = await claim_application_job(
        sessions, worker_id=f"consent-integration-{uuid4().hex[:10]}"
    )
    assert claim is not None and claim.owner_id == owner_id
    await process_application_job(
        sessions,
        claim,
        now=at,
        assistant=assistant,
        payment_support_contact="@pin_test_support",
    )
    async with sessions() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "telegram.reply.requested",
                OutboxEvent.causation_id == claim.job_id,
            )
        )
        assert event is not None
        payload = event.payload.get("payload", event.payload)
        result = dict(cast(dict[str, object], payload))
        result["_test_reply_event_id"] = event.id
        result["_test_source_job_id"] = claim.job_id
    delivery_at = max(at, event.created_at.astimezone(UTC))
    for _ in range(100):
        if not await process_delivery_iteration(
            sessions,
            delivery,
            worker_id="consent-integration-fake-delivery",
            now=lambda: delivery_at,
        ):
            break
    else:
        raise AssertionError("fake delivery worker did not become idle within the bounded drain")
    return owner_id, inbox_id, result


async def _text(
    sessions: async_sessionmaker[AsyncSession],
    delivery: _FakeTelegram,
    *,
    bot_id: int,
    telegram_user_id: int,
    value: str,
    at: datetime,
    assistant: BoundedPlanningAssistant,
) -> tuple[UUID, UUID, dict[str, object]]:
    return await _submit(
        sessions,
        delivery,
        bot_id=bot_id,
        telegram_user_id=telegram_user_id,
        update={
            "message": {
                "message_id": uuid4().int % 1_000_000,
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Fixture"},
                "chat": {"id": telegram_user_id, "type": "private"},
                "date": int(at.timestamp()),
                "text": value,
            }
        },
        at=at,
        assistant=assistant,
    )


async def _callback(
    sessions: async_sessionmaker[AsyncSession],
    delivery: _FakeTelegram,
    *,
    bot_id: int,
    telegram_user_id: int,
    value: str,
    at: datetime,
    assistant: BoundedPlanningAssistant,
) -> tuple[UUID, UUID, dict[str, object]]:
    return await _submit(
        sessions,
        delivery,
        bot_id=bot_id,
        telegram_user_id=telegram_user_id,
        update={
            "callback_query": {
                "id": f"consent-integration-{uuid4().hex}",
                "from": {"id": telegram_user_id, "is_bot": False, "first_name": "Fixture"},
                "message": {
                    "message_id": uuid4().int % 1_000_000,
                    "chat": {"id": telegram_user_id, "type": "private"},
                },
                "data": value,
            }
        },
        at=at,
        assistant=assistant,
    )


def _button(payload: dict[str, object], label: str) -> str:
    markup = payload.get("reply_markup")
    assert isinstance(markup, dict)
    rows = markup.get("inline_keyboard")
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, list)
        for item in row:
            if isinstance(item, dict) and item.get("text") == label:
                callback = item.get("callback_data")
                assert isinstance(callback, str)
                return callback
    raise AssertionError(f"button {label!r} was not present in the persisted reply")


async def _reservation_count(sessions: async_sessionmaker[AsyncSession], owner_id: UUID) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(UsageReservationRecord)
                .where(UsageReservationRecord.owner_id == owner_id)
            )
            or 0
        )


def _assistant(
    sessions: async_sessionmaker[AsyncSession], provider: _CountedInterpreter, at: datetime
) -> BoundedPlanningAssistant:
    return BoundedPlanningAssistant(
        transport=provider,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: at,
    )


@pytest.mark.parametrize("language", ("en", "ru"))
def test_initial_consent_flows_through_capture_and_reply_with_localized_deterministic_paths(
    consent_integration_database: str,
    language: str,
) -> None:
    if language not in {"ru", "en"}:
        pytest.fail("unsupported test language")

    async def scenario() -> None:
        engine = create_engine(consent_integration_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        bot_id = BOT_BASE + uuid4().int % 900_000_000
        telegram_user_id = USER_BASE + uuid4().int % 900_000_000
        provider = _CountedInterpreter(language)
        assistant = _assistant(sessions, provider, now)
        delivery = _FakeTelegram()
        try:
            owner_id, _, _ = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value="/start",
                at=now,
                assistant=assistant,
            )
            for answer in (
                language,
                "Asia/Almaty",
                "suggested"
                if language == "en"
                else "по умолчанию",
                "suggested"
                if language == "en"
                else "по умолчанию",
            ):
                await _text(
                    sessions,
                    delivery,
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    value=answer,
                    at=now,
                    assistant=assistant,
                )
            consent_prompt = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value="confirm"
                if language == "en"
                else "подтверждаю",
                at=now,
                assistant=assistant,
            )
            details_label = (
                "Details"
                if language == "en"
                else "Подробнее"
            )
            details = await _callback(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=_button(consent_prompt[2], details_label),
                at=now,
                assistant=assistant,
            )
            assert "OpenRouter" in str(details[2]["plain_text"])
            back_label = (
                "Back to setup"
                if language == "en"
                else "Вернуться к настройке"
            )
            await _callback(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=_button(details[2], back_label),
                at=now,
                assistant=assistant,
            )
            consent_prompt = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value="confirm"
                if language == "en"
                else "подтверждаю",
                at=now,
                assistant=assistant,
            )
            accept_label = (
                "Agree and finish setup"
                if language == "en"
                else "Согласиться и завершить настройку"
            )
            await _callback(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=_button(consent_prompt[2], accept_label),
                at=now,
                assistant=assistant,
            )
            async with sessions() as session:
                profile = await session.get(PreferencesRecord, owner_id)
                consent = await session.get(ProcessingConsentRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
            assert profile is not None and profile.language == language
            assert consent is not None and consent.state == "granted"
            assert trial is not None
            assert await _reservation_count(sessions, owner_id) == 0
            assert provider.calls == 0

            reply_start = len(delivery.replies)
            _, inbox_id, reply = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=(
                    "Schedule a review tomorrow at 9"
                    if language == "en"
                    else "Запланируй обзор на завтра в 9"
                ),
                at=now + timedelta(seconds=1),
                assistant=assistant,
            )
            assert provider.calls == 1
            assert (
                "Your plan is ready"
                if language == "en"
                else "План готов"
            ) in str(reply["plain_text"])
            new_ai_replies = [
                message
                for _, message in delivery.replies[reply_start:]
                if message.destination_id == telegram_user_id
                and message.plain_text == str(reply["plain_text"])
            ]
            recipient_bodies = [
                message.plain_text
                for _, message in delivery.replies[reply_start:]
                if message.destination_id == telegram_user_id
            ]
            assert len(new_ai_replies) == 1, (
                "expected one send matching the queued AI body; "
                f"queued={_bounded_text_repr(str(reply['plain_text']))}; "
                f"same-recipient final bodies (last 8 of {len(recipient_bodies)})="
                f"{[_bounded_text_repr(body) for body in recipient_bodies[-8:]]}"
            )
            reply_event_id = reply.get("_test_reply_event_id")
            source_job_id = reply.get("_test_source_job_id")
            assert isinstance(reply_event_id, UUID)
            assert isinstance(source_job_id, UUID)
            assert await _reservation_count(sessions, owner_id) == 1
            async with sessions() as session:
                inbox = await session.get(InboxUpdate, inbox_id)
                reply_event = await session.get(OutboxEvent, reply_event_id)
                attempts = list(
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.owner_id == owner_id,
                            DeliveryAttemptRecord.reply_event_id == reply_event_id,
                            DeliveryAttemptRecord.target_id == telegram_user_id,
                            DeliveryAttemptRecord.outcome == "confirmed",
                        )
                    )
                )
            assert inbox is not None and inbox.processing_grant_id == consent.grant_id
            assert (
                reply_event is not None
                and reply_event.owner_id == owner_id
                and reply_event.causation_id == source_job_id
            )
            assert len(attempts) == 1

            privacy = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value="/privacy",
                at=now + timedelta(seconds=2),
                assistant=assistant,
            )
            revoke_label = (
                "Withdraw consent"
                if language == "en"
                else "Отозвать согласие"
            )
            await _callback(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=_button(privacy[2], revoke_label),
                at=now + timedelta(seconds=3),
                assistant=assistant,
            )
            async with sessions() as session:
                withdrawn = await session.get(ProcessingConsentRecord, owner_id)
            assert withdrawn is not None and withdrawn.state == "revoked"
            reservations_before = await _reservation_count(sessions, owner_id)
            for command_text in (
                "/help",
                "/privacy",
                "/support",
                "/today",
                "/week",
                "/subscription",
                "/terms",
                "/paysupport",
                "/cancel",
                "/settings",
            ):
                await _text(
                    sessions,
                    delivery,
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    value=command_text,
                    at=now + timedelta(seconds=4),
                    assistant=assistant,
                )
            assert provider.calls == 1
            assert await _reservation_count(sessions, owner_id) == reservations_before
            async with sessions() as session:
                final_consent = await session.get(ProcessingConsentRecord, owner_id)
            assert final_consent is not None and final_consent.state == "revoked"
        finally:
            await engine.dispose()

    from pin.persistence.async_runner import run_local_async

    run_local_async(scenario)


@pytest.mark.parametrize("language", ("en", "ru"))
def test_legacy_profile_denial_then_privacy_acceptance_requires_a_new_input(
    consent_integration_database: str,
    language: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(consent_integration_database)
        sessions = create_session_factory(engine)
        now = datetime.now(UTC)
        bot_id = BOT_BASE + uuid4().int % 900_000_000
        telegram_user_id = USER_BASE + uuid4().int % 900_000_000
        period_id = f"legacy-trial-{uuid4().hex}"
        provider = _CountedInterpreter(language)
        assistant = _assistant(sessions, provider, now)
        delivery = _FakeTelegram()
        try:
            async with sessions() as session, session.begin():
                owner_id = await PostgresTelegramOwnerResolver().resolve(
                    session, bot_id=bot_id, telegram_user_id=telegram_user_id
                )
                session.add(
                    PreferencesRecord(
                        owner_id=owner_id,
                        language=language,
                        home_zone="Asia/Almaty",
                        reminder_calendar_days=1,
                        reminder_elapsed_hours=1,
                        quiet_start=time(22),
                        quiet_end=time(8),
                        review_time=time(20),
                        version=1,
                    )
                )
                session.add(AccessAccountRecord(owner_id=owner_id))
                session.add(
                    AccessPeriodRecord(
                        owner_id=owner_id,
                        period_id=period_id,
                        policy_kind="trial",
                        policy_version="legacy-fixture",
                        starts_at=now - timedelta(days=1),
                        ends_at=now + timedelta(days=5),
                        ai_turn_limit=250,
                        voice_second_limit=900,
                    )
                )
                session.add(
                    TrialActivationRecord(
                        owner_id=owner_id,
                        telegram_user_id=telegram_user_id,
                        period_id=period_id,
                        activated_at=now - timedelta(days=1),
                    )
                )

            denied = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=(
                    "Schedule a review tomorrow at 9"
                    if language == "en"
                    else "Запланировать обзор на завтра в 9"
                ),
                at=now,
                assistant=assistant,
            )
            assert "privacy" in str(denied[2]["plain_text"]).casefold()
            assert provider.calls == 0
            assert await _reservation_count(sessions, owner_id) == 0
            async with sessions() as session:
                inbox = await session.get(InboxUpdate, denied[1])
                consent = await session.get(ProcessingConsentRecord, owner_id)
                profile = await session.get(PreferencesRecord, owner_id)
                trial = await session.get(TrialActivationRecord, owner_id)
            assert inbox is not None and inbox.processing_grant_id is None
            assert consent is None
            assert profile is not None and profile.language == language
            assert trial is not None and trial.period_id == period_id

            privacy = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value="/privacy",
                at=now + timedelta(seconds=1),
                assistant=assistant,
            )
            grant_label = (
                "Agree and enable AI"
                if language == "en"
                else "Согласиться и включить ИИ"
            )
            await _callback(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=_button(privacy[2], grant_label),
                at=now + timedelta(seconds=2),
                assistant=assistant,
            )
            async with sessions() as session:
                consent = await session.get(ProcessingConsentRecord, owner_id)
                preserved_trial = await session.get(TrialActivationRecord, owner_id)
            assert consent is not None and consent.state == "granted"
            assert preserved_trial is not None and preserved_trial.period_id == period_id
            assert provider.calls == 0
            assert await _reservation_count(sessions, owner_id) == 0

            reply_start = len(delivery.replies)
            _, inbox_id, reply = await _text(
                sessions,
                delivery,
                bot_id=bot_id,
                telegram_user_id=telegram_user_id,
                value=(
                    "Schedule a review next week"
                    if language == "en"
                    else "Запланируй обзор на следующую неделю"
                ),
                at=now + timedelta(seconds=3),
                assistant=assistant,
            )
            assert provider.calls == 1
            assert await _reservation_count(sessions, owner_id) == 1
            new_ai_replies = [
                message
                for _, message in delivery.replies[reply_start:]
                if message.destination_id == telegram_user_id
                and message.plain_text == str(reply["plain_text"])
            ]
            recipient_bodies = [
                message.plain_text
                for _, message in delivery.replies[reply_start:]
                if message.destination_id == telegram_user_id
            ]
            assert len(new_ai_replies) == 1, (
                "expected one send matching the queued AI body; "
                f"queued={_bounded_text_repr(str(reply['plain_text']))}; "
                f"same-recipient final bodies (last 8 of {len(recipient_bodies)})="
                f"{[_bounded_text_repr(body) for body in recipient_bodies[-8:]]}"
            )
            reply_event_id = reply.get("_test_reply_event_id")
            source_job_id = reply.get("_test_source_job_id")
            assert isinstance(reply_event_id, UUID)
            assert isinstance(source_job_id, UUID)
            async with sessions() as session:
                inbox = await session.get(InboxUpdate, inbox_id)
                reply_event = await session.get(OutboxEvent, reply_event_id)
                attempts = list(
                    await session.scalars(
                        select(DeliveryAttemptRecord).where(
                            DeliveryAttemptRecord.owner_id == owner_id,
                            DeliveryAttemptRecord.reply_event_id == reply_event_id,
                            DeliveryAttemptRecord.target_id == telegram_user_id,
                            DeliveryAttemptRecord.outcome == "confirmed",
                        )
                    )
                )
                onboarding = await session.get(OnboardingRecord, owner_id)
            assert inbox is not None and inbox.processing_grant_id == consent.grant_id
            assert (
                reply_event is not None
                and reply_event.owner_id == owner_id
                and reply_event.causation_id == source_job_id
            )
            assert len(attempts) == 1
            assert onboarding is None
            assert (
                "Your plan is ready"
                if language == "en"
                else "План готов"
            ) in str(reply["plain_text"])
        finally:
            await engine.dispose()

    from pin.persistence.async_runner import run_local_async

    run_local_async(scenario)
