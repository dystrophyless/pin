from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.contexts.billing.api import (
    CheckAccess,
    ReadSubscriptionStatus,
    ReserveUsage,
    SqlAlchemyAccessUsageStore,
    StartTrial,
)
from pin.contexts.billing.domain.access import Operation, QuotaExceeded
from pin.contexts.billing.domain.payments import TERMS_VERSION
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessExpiryNoticeRecord,
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    RetainedBillingIdentityRecord,
    TermsAcceptanceRecord,
    TrialActivationRecord,
    UsageReservationRecord,
)
from pin.contexts.conversation.api import ConversationContext
from pin.contexts.conversation.infrastructure.models import (
    ConversationTurnResultRecord,
    ProviderCallRecord,
)
from pin.contexts.identity.domain.preferences import Preferences
from pin.contexts.identity.infrastructure.models import (
    OnboardingRecord,
    PreferencesRecord,
    ProcessingConsentRecord,
)
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.planning.infrastructure.models import PlanItemRecord
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.platform.jobs import JobClaim
from pin.platform.models import InboxUpdate, Job, OutboxEvent

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "pin_issue20_support_"
DATABASE_USER = "pin_test"
DATABASE_PORT = 51017
DATABASE_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
PROTECTED_DATABASES = {
    "pin_model_adapter_20261013",
    "pin_issue20_sandbox_20261014_25eb0b51",
    "pin_issue20_sandbox_20261014_33a6ed3b",
}
NOW = datetime(2026, 10, 15, 8, tzinfo=UTC)


def _support_database_url() -> str:
    value = os.getenv("PIN_TEST_ISSUE20_SUPPORT_DATABASE_URL")
    if not value:
        pytest.skip(
            "set PIN_TEST_ISSUE20_SUPPORT_DATABASE_URL to an owned issue20 support database"
        )
    url = make_url(value)
    if url.host != "127.0.0.1" or url.port != DATABASE_PORT:
        pytest.fail("Issue20 support proof requires the designated local PostgreSQL cluster")
    database = url.database or ""
    suffix = database.removeprefix(DATABASE_PREFIX).replace("_", "")
    if not database.startswith(DATABASE_PREFIX) or not suffix.isalnum():
        pytest.fail("Issue20 support proof requires its own pin_issue20_support_ database")
    if database in PROTECTED_DATABASES:
        pytest.fail("Issue20 support proof cannot use a protected database")

    engine = create_sync_engine(value)
    try:
        with engine.connect() as connection:
            identity = (
                str(connection.exec_driver_sql("select current_database()").scalar_one()),
                str(connection.exec_driver_sql("select current_user").scalar_one()),
                int(connection.exec_driver_sql("show port").scalar_one()),
                str(connection.exec_driver_sql("show data_directory").scalar_one()),
                str(connection.exec_driver_sql("show server_version").scalar_one()),
                int(connection.exec_driver_sql("show server_version_num").scalar_one()),
            )
    finally:
        engine.dispose()
    name, user, port, directory, version, version_num = identity
    if (name, user, port, version, version_num) != (
        database,
        DATABASE_USER,
        DATABASE_PORT,
        "18.6",
        180006,
    ):
        pytest.fail("Issue20 support PostgreSQL does not match the assigned database identity")
    if directory.replace("\\", "/").rstrip("/").casefold() != DATABASE_DIRECTORY.casefold():
        pytest.fail("Issue20 support proof is outside the assigned disposable cluster")
    return value


@pytest.fixture(scope="session")
def support_sessions() -> Iterator[async_sessionmaker[AsyncSession]]:
    database_url = _support_database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    yield create_session_factory(engine)
    # The external fixture manifest owns database cleanup; this test only closes its engine.
    run_local_async(engine.dispose)


def test_bilingual_terms_acceptance_and_payment_support_are_persisted_routes(
    support_sessions: async_sessionmaker[AsyncSession],
) -> None:
    ru_owner, en_owner = uuid4(), uuid4()
    expired_ru_owner, expired_en_owner = uuid4(), uuid4()
    ru_user = 741_000_000 + ru_owner.int % 10_000_000
    en_user = 751_000_000 + en_owner.int % 10_000_000
    expired_ru_user = 761_000_000 + expired_ru_owner.int % 10_000_000
    expired_en_user = 771_000_000 + expired_en_owner.int % 10_000_000
    bot_id = 876_543 + ru_owner.int % 100_000
    worker_id = f"issue20-support-{uuid4().hex[:12]}"

    async def seed_owner(owner_id: UUID, telegram_user_id: int, language: str) -> None:
        async with support_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id
                )
            )
            await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, telegram_user_id, NOW)
            )
            saved = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(language=language, home_zone="UTC"),
            )
            assert saved is not None

    async def seed_expired_owner(owner_id: UUID, telegram_user_id: int, language: str) -> None:
        async with support_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id
                )
            )
            await SqlAlchemyAccessUsageStore(session).activate_trial(
                StartTrial(owner_id, telegram_user_id, NOW - timedelta(days=31))
            )
            saved = await SqlAlchemyIdentityStore(session).save_preferences(
                owner_id=owner_id,
                expected_version=None,
                preferences=Preferences(language=language, home_zone="UTC"),
            )
            assert saved is not None

    async def command_route(
        update_id: int, owner_id: UUID, user_id: int, command_text: str
    ) -> dict[str, Any]:
        async with support_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload={
                    "update_id": update_id,
                    "message": {
                        "message_id": update_id,
                        "date": int(NOW.timestamp()),
                        "chat": {"id": user_id, "type": "private"},
                        "from": {"id": user_id, "is_bot": False},
                        "text": command_text,
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted
            mapping = await session.get(TelegramOwnerRecord, (bot_id, user_id))
            assert mapping is not None and mapping.owner_id == owner_id
            inbox = await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
                )
            )
            assert inbox is not None and inbox.owner_id == owner_id

        claim = await claim_application_job(support_sessions, worker_id=worker_id)
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(
            support_sessions,
            claim,
            now=NOW,
            assistant=None,
            payment_support_contact=None,
        )
        async with support_sessions() as session:
            saved_job = await session.get(Job, claim.job_id)
            assert saved_job is not None and saved_job.status == "succeeded"
            replies = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.owner_id == owner_id,
                        )
                    )
                ).all()
            )
            assert len(replies) == 1
            envelope = replies[0].payload
            assert envelope["event_type"] == "telegram.reply.requested"
            assert envelope["owner_id"] == str(owner_id)
            reply_payload = envelope["payload"]
            assert reply_payload["chat_id"] == user_id
            assert reply_payload["source_job_id"] == str(claim.job_id)
            return cast(dict[str, Any], reply_payload)

    async def scenario() -> None:
        await seed_owner(ru_owner, ru_user, "ru")
        await seed_owner(en_owner, en_user, "en")
        await seed_expired_owner(expired_ru_owner, expired_ru_user, "ru")
        await seed_expired_owner(expired_en_owner, expired_en_user, "en")

        for update_id, owner_id, user_id, command_text, expected in (
            (20, expired_ru_owner, expired_ru_user, "/help", ("/today", "/support")),
            (
                21,
                expired_ru_owner,
                expired_ru_user,
                "/support",
                ("Связь с поддержкой:", "@pin_appbot"),
            ),
            (
                22,
                expired_ru_owner,
                expired_ru_user,
                "/paysupport",
                ("Возвраты рассматриваются вручную", "укажите Telegram charge ID"),
            ),
            (23, expired_en_owner, expired_en_user, "/help", ("today's plans", "/support")),
            (
                24,
                expired_en_owner,
                expired_en_user,
                "/support",
                ("Contact support:", "@pin_appbot"),
            ),
            (
                25,
                expired_en_owner,
                expired_en_user,
                "/paysupport",
                ("Refunds are reviewed manually", "Telegram charge ID"),
            ),
        ):
            reply = await command_route(update_id, owner_id, user_id, command_text)
            assert all(marker in reply["plain_text"] for marker in expected)
            async with support_sessions() as session:
                status = await SqlAlchemyAccessUsageStore(session).read_subscription_status(
                    ReadSubscriptionStatus(owner_id, NOW)
                )
                assert not status.active
                assert not (
                    await session.scalars(
                        select(AccessPeriodRecord).where(
                            AccessPeriodRecord.owner_id == owner_id,
                            AccessPeriodRecord.policy_kind == "paid",
                        )
                    )
                ).all()
                assert not (
                    await session.scalars(
                        select(UsageReservationRecord).where(
                            UsageReservationRecord.owner_id == owner_id
                        )
                    )
                ).all()
                assert not (
                    await session.scalars(
                        select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                    )
                ).all()

        foreign_acceptance = await command_route(1, en_owner, en_user, "/acceptterms")
        assert "read the subscription terms first" in foreign_acceptance["plain_text"]
        async with support_sessions() as session:
            assert await session.get(TermsAcceptanceRecord, (en_owner, TERMS_VERSION)) is None

        ru_terms = await command_route(2, ru_owner, ru_user, "/terms")
        for required in (
            "250 Telegram Stars за 30 дней",
            "автоматически продлевается",
            "/cancel",
            "1 000 обращений к ИИ",
            "60 минут обработки голоса",
            "без переноса остатка",
            "/paysupport",
            "/acceptterms",
        ):
            assert required in ru_terms["plain_text"]
        async with support_sessions() as session:
            ru_terms_row = await session.get(TermsAcceptanceRecord, (ru_owner, TERMS_VERSION))
            assert ru_terms_row is not None and ru_terms_row.viewed_at == NOW
            assert ru_terms_row.accepted_at is None

        denied_order = await command_route(3, ru_owner, ru_user, "/buy")
        assert "/terms" in denied_order["plain_text"]
        async with support_sessions() as session:
            assert not (
                await session.scalars(
                    select(PaymentOrderRecord).where(PaymentOrderRecord.owner_id == ru_owner)
                )
            ).all()
            assert not (
                await session.scalars(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == ru_owner,
                        AccessPeriodRecord.policy_kind == "paid",
                    )
                )
            ).all()
            assert not (
                await session.scalars(
                    select(PaymentReceiptRecord).where(PaymentReceiptRecord.owner_id == ru_owner)
                )
            ).all()

        en_terms = await command_route(4, en_owner, en_user, "/terms")
        for required in (
            "250 Telegram Stars for each 30-day period",
            "renews automatically",
            "/cancel",
            "1,000 AI turns",
            "60 minutes of voice processing",
            "no rollover",
            "/paysupport",
            "/acceptterms",
        ):
            assert required in en_terms["plain_text"]

        en_accepted = await command_route(5, en_owner, en_user, "/acceptterms")
        assert "Terms accepted" in en_accepted["plain_text"]
        async with support_sessions() as session:
            accepted_terms = await session.get(TermsAcceptanceRecord, (en_owner, TERMS_VERSION))
            assert accepted_terms is not None
            assert accepted_terms.viewed_at == NOW and accepted_terms.accepted_at == NOW
            ru_terms_row = await session.get(TermsAcceptanceRecord, (ru_owner, TERMS_VERSION))
            assert ru_terms_row is not None and ru_terms_row.accepted_at is None

        pending_order = await command_route(6, en_owner, en_user, "/buy")
        assert "no payment is confirmed yet" in pending_order["plain_text"]
        async with support_sessions() as session:
            orders = list(
                (
                    await session.scalars(
                        select(PaymentOrderRecord).where(PaymentOrderRecord.owner_id == en_owner)
                    )
                ).all()
            )
            assert len(orders) == 1 and orders[0].invoice_status == "queued"
            assert not (
                await session.scalars(
                    select(PaymentReceiptRecord).where(PaymentReceiptRecord.owner_id == en_owner)
                )
            ).all()
            assert not (
                await session.scalars(
                    select(AccessPeriodRecord).where(
                        AccessPeriodRecord.owner_id == en_owner,
                        AccessPeriodRecord.policy_kind == "paid",
                    )
                )
            ).all()

        ru_support = await command_route(7, ru_owner, ru_user, "/paysupport")
        assert "укажите Telegram charge ID" in ru_support["plain_text"]
        assert "Возвраты рассматриваются вручную" in ru_support["plain_text"]
        assert "банковские данные" in ru_support["plain_text"]
        en_support = await command_route(8, en_owner, en_user, "/paysupport")
        assert "Telegram charge ID" in en_support["plain_text"]
        assert "reviewed manually" in en_support["plain_text"]
        assert "Do not send bank details" in en_support["plain_text"]

    run_local_async(scenario)


def test_privacy_is_a_durable_private_command_for_every_access_state(
    support_sessions: async_sessionmaker[AsyncSession],
) -> None:
    owner_ids = {
        "no_profile": uuid4(),
        "draft_ru": uuid4(),
        "expired_ru": uuid4(),
        "expired_en": uuid4(),
        "exhausted_ru": uuid4(),
        "exhausted_en": uuid4(),
    }
    telegram_users = {
        "no_profile": 781_000_000 + owner_ids["no_profile"].int % 10_000_000,
        "draft_ru": 782_000_000 + owner_ids["draft_ru"].int % 10_000_000,
        "expired_ru": 783_000_000 + owner_ids["expired_ru"].int % 10_000_000,
        "expired_en": 784_000_000 + owner_ids["expired_en"].int % 10_000_000,
        "exhausted_ru": 785_000_000 + owner_ids["exhausted_ru"].int % 10_000_000,
        "exhausted_en": 786_000_000 + owner_ids["exhausted_en"].int % 10_000_000,
    }
    bot_id = 987_654 + owner_ids["no_profile"].int % 100_000
    worker_id = f"issue20-privacy-{uuid4().hex[:12]}"
    domain_records = (
        AccessAccountRecord,
        AccessExpiryNoticeRecord,
        AccessPeriodRecord,
        RetainedBillingIdentityRecord,
        TrialActivationRecord,
        UsageReservationRecord,
        PaymentOrderRecord,
        PaymentReceiptRecord,
        TermsAcceptanceRecord,
        PreferencesRecord,
        OnboardingRecord,
        PlanItemRecord,
        ProviderCallRecord,
        ConversationTurnResultRecord,
        DeletionRequestRecord,
        DeletionTombstoneRecord,
    )

    class UnexpectedAssistant:
        async def interpret(
            self,
            context: ConversationContext,
            *,
            at: datetime,
            claim: JobClaim | None = None,
        ) -> NoReturn:
            raise AssertionError("the deterministic /privacy command must not call the model")

    async def snapshot_owner(session: AsyncSession, owner_id: UUID) -> dict[str, tuple[Any, ...]]:
        snapshot: dict[str, tuple[Any, ...]] = {}
        for model in domain_records:
            primary_key = tuple(
                getattr(model, column.key) for column in model.__table__.primary_key
            )
            rows = (
                await session.scalars(
                    select(model)
                    .where(model.owner_id == owner_id)
                    .order_by(*primary_key)
                )
            ).all()
            snapshot[model.__name__] = tuple(
                tuple(getattr(row, column.key) for column in model.__table__.columns)
                for row in rows
            )
        return snapshot

    async def seed_identity(
        key: str, *, language: str | None = None, trial_at: datetime | None = None,
        exhausted: bool = False, draft_ru: bool = False
    ) -> None:
        owner_id = owner_ids[key]
        telegram_user_id = telegram_users[key]
        async with support_sessions() as session, session.begin():
            session.add(
                TelegramOwnerRecord(
                    bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id
                )
            )
            if trial_at is not None:
                billing = SqlAlchemyAccessUsageStore(session)
                trial = await billing.activate_trial(
                    StartTrial(owner_id, telegram_user_id, trial_at)
                )
                assert trial.period_id is not None
                saved = await SqlAlchemyIdentityStore(session).save_preferences(
                    owner_id=owner_id,
                    expected_version=None,
                    preferences=Preferences(language=language or "en", home_zone="UTC"),
                )
                assert saved is not None
                if exhausted:
                    period = await session.get(AccessPeriodRecord, (owner_id, trial.period_id))
                    assert period is not None
                    period.ai_turn_limit = 1
                    await session.flush()
                    reservation = await billing.reserve_usage(
                        ReserveUsage(
                            owner_id=owner_id,
                            request_id=f"privacy-exhausted-{key}",
                            operation=Operation.AI_TURN,
                            ai_turns=1,
                            voice_seconds=0,
                            at=NOW,
                            expires_at=NOW + timedelta(minutes=5),
                        )
                    )
                    assert reservation.created and reservation.access.allowed
                    await billing.settle_usage(
                        owner_id=owner_id,
                        request_id=f"privacy-exhausted-{key}",
                        ai_turns=1,
                        voice_seconds=0,
                        at=NOW,
                    )
                    remaining_access = await billing.check_access(
                        CheckAccess(owner_id, Operation.AI_TURN, NOW)
                    )
                    assert remaining_access.allowed
                    with pytest.raises(QuotaExceeded, match="active period allowance"):
                        await billing.reserve_usage(
                            ReserveUsage(
                                owner_id=owner_id,
                                request_id=f"privacy-exhaustion-probe-{key}",
                                operation=Operation.AI_TURN,
                                ai_turns=1,
                                voice_seconds=0,
                                at=NOW,
                                expires_at=NOW + timedelta(minutes=5),
                            )
                        )
            elif draft_ru:
                identity = SqlAlchemyIdentityStore(session)
                draft = await identity.begin_onboarding(
                    owner_id=owner_id,
                    mode="initial",
                    initial_values={},
                    base_profile_version=None,
                )
                advanced = await identity.advance_onboarding(
                    draft, step="home_zone", values={"language": "ru"}
                )
                assert advanced is not None and advanced.step == "home_zone"

    def payload_for(update_id: int, user_id: int, command_text: str) -> dict[str, Any]:
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": int(NOW.timestamp()),
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False},
                "text": command_text,
            },
        }

    async def privacy_route(
        update_id: int, key: str, command_text: str
    ) -> tuple[dict[str, Any], UUID]:
        owner_id = owner_ids[key]
        telegram_user_id = telegram_users[key]
        async with support_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload=payload_for(update_id, telegram_user_id, command_text),
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted
            inbox = await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
                )
            )
            assert inbox is not None and inbox.owner_id == owner_id

        claim = await claim_application_job(support_sessions, worker_id=worker_id)
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(
            support_sessions,
            claim,
            now=NOW,
            assistant=UnexpectedAssistant(),
            payment_support_contact=None,
        )
        async with support_sessions() as session:
            saved_job = await session.get(Job, claim.job_id)
            assert saved_job is not None and saved_job.status == "succeeded"
            replies = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.causation_id == claim.job_id,
                            OutboxEvent.owner_id == owner_id,
                        )
                    )
                ).all()
            )
            assert len(replies) == 1
            envelope = replies[0].payload
            reply_payload = envelope["payload"]
            assert reply_payload["chat_id"] == telegram_user_id
            assert reply_payload["source_job_id"] == str(claim.job_id)
            return cast(dict[str, Any], reply_payload), claim.job_id

    async def consent_callback_route(
        update_id: int, key: str, callback_data: str
    ) -> dict[str, Any]:
        owner_id = owner_ids[key]
        telegram_user_id = telegram_users[key]
        callback_payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"privacy-consent-{update_id}",
                "from": {"id": telegram_user_id, "is_bot": False},
                "message": {
                    "message_id": update_id,
                    "chat": {"id": telegram_user_id, "type": "private"},
                },
                "data": callback_data,
            },
        }
        async with support_sessions() as session, session.begin():
            receipt = await persist_update(
                session,
                bot_id=bot_id,
                payload=callback_payload,
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert receipt is not None and receipt.accepted
        claim = await claim_application_job(support_sessions, worker_id=worker_id)
        assert claim is not None and claim.owner_id == owner_id
        await process_application_job(
            support_sessions,
            claim,
            now=NOW,
            assistant=UnexpectedAssistant(),
            payment_support_contact=None,
        )
        async with support_sessions() as session:
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.causation_id == claim.job_id,
                    OutboxEvent.owner_id == owner_id,
                )
            )
            assert event is not None
            return cast(dict[str, Any], event.payload["payload"])

    async def scenario() -> None:
        await seed_identity("no_profile")
        await seed_identity("draft_ru", draft_ru=True)
        await seed_identity("expired_ru", language="ru", trial_at=NOW - timedelta(days=31))
        await seed_identity("expired_en", language="en", trial_at=NOW - timedelta(days=31))
        await seed_identity("exhausted_ru", language="ru", trial_at=NOW, exhausted=True)
        await seed_identity("exhausted_en", language="en", trial_at=NOW, exhausted=True)

        cases = (
            (9001, "no_profile", "/privacy", "Pin is an independent planning service"),
            (9002, "draft_ru", "/PRIVACY@PIN_APPBOT", "самостоятельный сервис планирования"),
            (9003, "expired_ru", "/privacy@pin_appbot", "самостоятельный сервис планирования"),
            (9004, "expired_en", "/privacy", "Pin is an independent planning service"),
            (9005, "exhausted_ru", "/privacy", "самостоятельный сервис планирования"),
            (9006, "exhausted_en", "/privacy", "Pin is an independent planning service"),
        )
        baseline: dict[str, dict[str, tuple[Any, ...]]] = {}
        for key, owner_id in owner_ids.items():
            async with support_sessions() as session:
                baseline[key] = await snapshot_owner(session, owner_id)

        for update_id, key, command_text, language_marker in cases:
            owner_id = owner_ids[key]
            reply, job_id = await privacy_route(update_id, key, command_text)
            assert language_marker in reply["plain_text"]
            assert "OpenRouter" in reply["plain_text"]
            assert "24 " in reply["plain_text"]
            assert r"/delete\_account" in reply["markdown"]
            assert len(reply["plain_text"]) < 4096
            assert len(reply["markdown"]) < 4096
            if key == "no_profile":
                async with support_sessions() as session:
                    assert await session.get(AccessAccountRecord, owner_id) is None
                    assert await session.get(TrialActivationRecord, owner_id) is None
                    assert await session.get(PreferencesRecord, owner_id) is None

            async with support_sessions() as session:
                assert await snapshot_owner(session, owner_id) == baseline[key]
                if update_id == 9001:
                    reply_count = await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.owner_id == owner_id,
                        )
                    )
                    assert reply_count == 1

            if update_id == 9001:
                duplicate_payload = payload_for(
                    update_id, telegram_users[key], command_text
                )
                async with support_sessions() as session, session.begin():
                    duplicate = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=duplicate_payload,
                        owner_resolver=PostgresTelegramOwnerResolver(),
                    )
                    assert duplicate is not None and not duplicate.accepted
                    assert duplicate.update_id == update_id
                    inbox = await session.scalar(
                        select(InboxUpdate).where(
                            InboxUpdate.bot_id == bot_id,
                            InboxUpdate.update_id == update_id,
                        )
                    )
                    assert inbox is not None
                    job_ids = list(
                        (
                            await session.scalars(
                                select(Job.id).where(
                                    Job.lane == "application",
                                    Job.deduplication_key == f"telegram:{bot_id}:{update_id}",
                                )
                            )
                        ).all()
                    )
                    assert job_ids == [job_id]
                assert await claim_application_job(
                    support_sessions, worker_id=worker_id
                ) is None
                async with support_sessions() as session:
                    reply_count = await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(
                            OutboxEvent.event_type == "telegram.reply.requested",
                            OutboxEvent.owner_id == owner_ids[key],
                        )
                    )
                    assert reply_count == 1
                    assert await snapshot_owner(session, owner_id) == baseline[key]

        async with support_sessions() as session, session.begin():
            group = await persist_update(
                session,
                bot_id=bot_id,
                payload={
                    "update_id": 9099,
                    "message": {
                        "message_id": 9099,
                        "date": int(NOW.timestamp()),
                        "chat": {"id": -9099, "type": "group"},
                        "from": {"id": telegram_users["no_profile"], "is_bot": False},
                        "text": "/privacy",
                    },
                },
                owner_resolver=PostgresTelegramOwnerResolver(),
            )
            assert group is None
            assert await session.scalar(
                select(InboxUpdate).where(
                    InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == 9099
                )
            ) is None

        # Existing users can grant, withdraw, and regrant without changing saved
        # preferences, Billing access, or the exhausted allowance.
        owner_id = owner_ids["exhausted_en"]
        before = baseline["exhausted_en"]
        privacy_reply, _ = await privacy_route(9_100, "exhausted_en", "/privacy")

        def consent_token(reply: dict[str, Any], label: str) -> str:
            keyboard = reply["reply_markup"]["inline_keyboard"]
            return next(
                button["callback_data"]
                for row in keyboard
                for button in row
                if button["text"] == label
            )

        first_grant = await consent_callback_route(
            9_101,
            "exhausted_en",
            consent_token(privacy_reply, "Agree and enable AI"),
        )
        assert "Consent enabled" in first_grant["plain_text"]
        async with support_sessions() as session:
            first = await session.get(ProcessingConsentRecord, owner_id)
            assert first is not None and first.state == "granted" and first.revision == 1
            first_grant_id = first.grant_id
            assert await snapshot_owner(session, owner_id) == before

        privacy_reply, _ = await privacy_route(9_102, "exhausted_en", "/privacy")
        withdrawn = await consent_callback_route(
            9_103,
            "exhausted_en",
            consent_token(privacy_reply, "Withdraw consent"),
        )
        assert "already started may finish" in withdrawn["plain_text"]
        async with support_sessions() as session:
            revoked = await session.get(ProcessingConsentRecord, owner_id)
            assert revoked is not None and revoked.state == "revoked" and revoked.revision == 2
            assert revoked.grant_id == first_grant_id
            assert await snapshot_owner(session, owner_id) == before

        privacy_reply, _ = await privacy_route(9_104, "exhausted_en", "/privacy")
        reaccepted = await consent_callback_route(
            9_105,
            "exhausted_en",
            consent_token(privacy_reply, "Agree and enable AI"),
        )
        assert "please resend it" in reaccepted["plain_text"]
        async with support_sessions() as session:
            regranted = await session.get(ProcessingConsentRecord, owner_id)
            assert regranted is not None
            assert regranted.state == "granted" and regranted.revision == 3
            assert regranted.grant_id is not None and regranted.grant_id != first_grant_id
            assert await snapshot_owner(session, owner_id) == before

    run_local_async(scenario)
