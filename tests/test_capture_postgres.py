from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from processing_consent_fixtures import seed_accepted_processing_consent
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as sqlalchemy_create_async_engine

from pin.adapters.telegram.application_worker import (
    claim_application_job,
    process_application_job,
)
from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerRecord,
    persist_update,
)
from pin.adapters.telegram.presentation.capture import capture_confirmation_callback_data
from pin.contexts.billing.api import SqlAlchemyAccessUsageStore
from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import AccessAccountRecord, AccessPeriodRecord
from pin.contexts.conversation.application.capture import (
    CaptureInterpretation,
    CaptureTurnReply,
    apply_clarification_choice,
    confirm_ready_draft,
    finish_interpretation,
    prepare_interpretation,
    restore_ready_preview,
)
from pin.contexts.conversation.application.contracts import CaptureProofPart
from pin.contexts.conversation.application.dto import (
    ActionProposal,
    ConversationContext,
    ModelFailure,
    ModelSuccess,
    ProposalBatch,
)
from pin.contexts.conversation.domain.capture import (
    CaptureConflict,
    CaptureDraft,
    finish_collecting,
    finish_ready,
)
from pin.contexts.conversation.domain.proposals import FailureCategory, InputKind, SourceLanguage
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureConfirmationRecord,
    CaptureDraftRecord,
)
from pin.contexts.conversation.infrastructure.capture_store import (
    SqlAlchemyCaptureConfirmationVerifier,
    SqlAlchemyCaptureRecurrenceConfirmationVerifier,
    SqlAlchemyCaptureStore,
)
from pin.contexts.conversation.infrastructure.models import ProviderCallRecord
from pin.contexts.identity.api import (
    PreferencesSnapshot,
    ProcessingAuthorization,
    authorize_processing_consent,
    get_processing_consent,
    revoke_processing_consent,
)
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.infrastructure.models import (
    NotificationReconciliationReceiptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.api import (
    RecurrenceCommandStore,
    RecurrenceProjection,
    RecurringPlanDraft,
    ReminderOffset,
)
from pin.contexts.planning.application.commands import AccessDenied, ConfirmationRejected
from pin.contexts.planning.application.contracts import ConfirmationProof
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.contexts.planning.infrastructure.series_store import SqlAlchemySeriesStore
from pin.contexts.planning.infrastructure.store import SqlAlchemyPlanningStore
from pin.persistence import SqlAlchemyUnitOfWork, run_local_async
from pin.persistence.db import create_engine as create_async_engine
from pin.persistence.db import create_session_factory
from pin.persistence.models import Base
from pin.platform.models import Job, OutboxEvent


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


class FailAfterSeriesPersistStore:
    """Raise after the recurring half has flushed to prove the caller UoW rolls back."""

    def __init__(self, store: RecurrenceCommandStore) -> None:
        self._store = store

    async def lock_action(self, owner_id: UUID, action_key: str) -> None:
        await self._store.lock_action(owner_id, action_key)

    async def get_receipt(self, owner_id: UUID, action_key: str) -> dict[str, object] | None:
        return await self._store.get_receipt(owner_id, action_key)

    async def persist_series(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        approved_preferences_version: int,
        draft: RecurringPlanDraft,
        projection: RecurrenceProjection,
        effective_reminders: tuple[ReminderOffset, ...],
        default_zone: str,
        quiet_zone: str,
        quiet_start: time,
        quiet_end: time,
        at: datetime,
        horizon_date: date,
    ) -> dict[str, object]:
        await self._store.persist_series(
            owner_id=owner_id,
            action_key=action_key,
            payload_sha256=payload_sha256,
            approved_preferences_version=approved_preferences_version,
            draft=draft,
            projection=projection,
            effective_reminders=effective_reminders,
            default_zone=default_zone,
            quiet_zone=quiet_zone,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            at=at,
            horizon_date=horizon_date,
        )
        raise RuntimeError("simulated failure after recurring schedule and events were flushed")


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATA_DIRECTORY = "C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013"
DATABASE_PREFIX = "pin_issue14_"


def _safe_database_url() -> str:
    database_url = os.getenv("PIN_TEST_CAPTURE_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_CAPTURE_DATABASE_URL to the assigned disposable capture database")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            database_name = connection.exec_driver_sql("select current_database()").scalar_one()
            data_directory = connection.exec_driver_sql("show data_directory").scalar_one()
    finally:
        engine.dispose()
    if not str(database_name).startswith(DATABASE_PREFIX):
        pytest.fail("capture PostgreSQL database name is outside the assigned prefix")
    if str(data_directory).replace("\\", "/") != EXPECTED_DATA_DIRECTORY:
        pytest.fail("capture PostgreSQL data directory is not the assigned disposable cluster")
    return database_url


@pytest.fixture(scope="session")
def capture_database() -> Iterator[str]:
    database_url = _safe_database_url()
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    yield database_url


@pytest.fixture(autouse=True)
def clean_capture_test_rows(capture_database: str) -> Iterator[None]:
    """Keep globally pumped capture outbox events isolated between tests."""
    async def cleanup() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                for table in reversed(
                    [table for table in Base.metadata.sorted_tables if table.schema]
                ):
                    await session.execute(delete(table))
        finally:
            await engine.dispose()

    run_local_async(cleanup)
    yield
    run_local_async(cleanup)


def test_capture_draft_restart_dedup_fencing_and_one_use_proof(capture_database: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        expires = now + timedelta(hours=24)
        digest = "d" * 64
        credential: str
        ready_id = uuid4()
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                begun = await store.begin(
                    owner_id=owner_id,
                    input_key="telegram-update:1001",
                    text="Schedule the review",
                    at=now,
                    expires_at=expires,
                )
                assert begun.is_new_input
                ready = finish_ready(
                    begun.draft,
                    request_key="telegram-update:1001",
                    actions=({"kind": "proposal", "title": "Review"},),
                    preview={"items": []},
                    projection_sha256=digest,
                    reply="Review and confirm",
                    at=now + timedelta(seconds=1),
                )
                credential = await store.issue_proofs(
                    owner_id=owner_id,
                    draft_id=ready.id,
                    preview_version=ready.version,
                    parts=(CaptureProofPart("one_off", ready_id, digest),),
                    issued_at=now + timedelta(seconds=1),
                    expires_at=expires,
                )
                await store.persist_draft(ready, expected_version=begun.draft.version)
                await store.complete_input(
                    owner_id=owner_id,
                    input_key="telegram-update:1001",
                    state="complete",
                    response={"kind": "preview", "text": "Review and confirm"},
                    at=now + timedelta(seconds=1),
                )
                draft_id = ready.id
                ready_version = ready.version

            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                restored = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
                assert restored is not None and restored.state.value == "ready"
                assert restored.version == ready_version
                receipt = await store.begin(
                    owner_id=owner_id,
                    input_key="telegram-update:1001",
                    text="ignored duplicate body",
                    at=now + timedelta(seconds=2),
                    expires_at=expires,
                )
                assert not receipt.is_new_input
                assert receipt.draft.id == draft_id
                located = await store.locate_confirmation(
                    owner_id=owner_id, credential=credential, at=now + timedelta(seconds=2)
                )
                assert located == (draft_id, ready_version)
                verifier = SqlAlchemyCaptureConfirmationVerifier(session)
                proof = ConfirmationProof(ready_id, ready_version, credential)
                grant = await verifier.verify_and_consume(
                    proof,
                    owner_id=owner_id,
                    approved_projection_sha256=digest,
                    at=now + timedelta(seconds=2),
                )
                assert grant is not None and grant.owner_id == owner_id
                replay = await verifier.verify_and_consume(
                    proof,
                    owner_id=owner_id,
                    approved_projection_sha256=digest,
                    at=now + timedelta(seconds=2),
                )
                assert replay is None

            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                changed = await store.begin(
                    owner_id=owner_id,
                    input_key="telegram-update:1002",
                    text="Actually make it Friday",
                    at=now + timedelta(seconds=3),
                    expires_at=expires,
                )
                assert changed.draft.state.value == "processing"
                assert changed.draft.version == ready_version + 1
                assert changed.draft.preview is None
                assert (
                    await store.locate_confirmation(
                        owner_id=owner_id,
                        credential=credential,
                        at=now + timedelta(seconds=3),
                    )
                    is None
                )

            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                expired = await store.begin(
                    owner_id=owner_id,
                    input_key="telegram-update:1003",
                    text="Start over after expiry",
                    at=expires + timedelta(seconds=1),
                    expires_at=expires + timedelta(hours=1),
                )
                assert expired.draft.id != draft_id
                assert expired.draft.state.value == "processing"
                old = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
                assert old is not None and old.state.value == "expired"
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_deterministic_time_choice_persists_ready_preview_and_version_bound_proof(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        try:
            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                begun = await store.begin(
                    owner_id=owner_id,
                    input_key="telegram-update:choice-1",
                    text="Exercise on Friday",
                    at=now,
                    expires_at=now + timedelta(hours=24),
                )
                action: dict[str, object] = {
                    "kind": "proposal",
                    "language": "en",
                    "operation": "propose_create_plan",
                    "fields": {
                        "title": "Exercise",
                        "schedule": {
                            "kind": "task",
                            "local_date": "2026-10-16",
                            "timing": "unspecified",
                        },
                    },
                }
                collecting = finish_collecting(
                    begun.draft,
                    request_key="telegram-update:choice-1",
                    reply="Choose a time or keep this untimed.",
                    actions=(action,),
                    at=now + timedelta(seconds=1),
                )
                await store.persist_draft(collecting, expected_version=begun.draft.version)
                await store.complete_input(
                    owner_id=owner_id,
                    input_key="telegram-update:choice-1",
                    state="complete",
                    response={"kind": "reply"},
                    at=now + timedelta(seconds=1),
                )
                draft_id = collecting.id
                draft_version = collecting.version

            async with sessions() as session, session.begin():
                store = SqlAlchemyCaptureStore(session)
                restored = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
                assert restored is not None and restored.state.value == "collecting"
                assert restored.version == draft_version and restored.normalized_actions
                preferences = PreferencesSnapshot(
                    owner_id=owner_id,
                    language="en",
                    home_zone="Asia/Qyzylorda",
                    reminder_calendar_days=0,
                    reminder_elapsed_hours=0,
                    quiet_start=time(23),
                    quiet_end=time(7),
                    review_time=time(18),
                    version=1,
                )
                with pytest.raises(CaptureConflict):
                    await apply_clarification_choice(
                        store,
                        owner_id=owner_id,
                        draft_id=draft_id,
                        expected_version=draft_version - 1,
                        choice_id="task:0:preferred_time",
                        preferences=preferences,
                        at=now + timedelta(seconds=2),
                    )
                reply = await apply_clarification_choice(
                    store,
                    owner_id=owner_id,
                    draft_id=draft_id,
                    expected_version=draft_version,
                    choice_id="task:0:preferred_time",
                    preferences=preferences,
                    at=now + timedelta(seconds=2),
                )
                assert reply.state == "ready"
                assert reply.confirmation_credential
                saved = await store.get_draft(owner_id=owner_id, draft_id=draft_id)
                assert saved is not None and saved.state.value == "ready"
                assert saved.preview is not None
                preview_items = cast(list[dict[str, object]], saved.preview["items"])
                item = preview_items[0]
                details = cast(dict[str, object], item["details"])
                assert details["local_time"] == "18:00:00"
                assert await store.locate_confirmation(
                    owner_id=owner_id,
                    credential=reply.confirmation_credential,
                    at=now + timedelta(seconds=3),
                ) == (saved.id, saved.version)
        finally:
            await engine.dispose()

    run_local_async(scenario)


async def _seed_capture_owner(
    sessions: async_sessionmaker[AsyncSession], owner_id: UUID, *, active_until: datetime
) -> ProcessingAuthorization:
    now = datetime(2026, 10, 14, 8, tzinfo=UTC)
    async with sessions() as session, session.begin():
        authorization = await seed_accepted_processing_consent(
            session,
            owner_id=owner_id,
            bot_id=abs(owner_id.int % 2_000_000_000) + 1,
            telegram_user_id=abs(owner_id.int // 2 % 2_000_000_000) + 1,
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
        session.add(AccessAccountRecord(owner_id=owner_id))
        session.add(
            AccessPeriodRecord(
                owner_id=owner_id,
                period_id=f"trial-{uuid4().hex}",
                policy_kind=PeriodKind.TRIAL.value,
                policy_version="trial-v1",
                starts_at=now - timedelta(days=1),
                ends_at=active_until,
                ai_turn_limit=250,
                voice_second_limit=900,
            )
        )
    return authorization


def _mixed_proposal_batch() -> ProposalBatch:
    actions = (
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "One-off review",
                "schedule": {
                    "kind": "task",
                    "local_date": "2026-10-16",
                    "local_time": "09:00:00",
                    "timezone": "UTC",
                    "timing": "timed",
                },
                "reminders": [
                    {
                        "kind": "relative",
                        "basis": "elapsed",
                        "amount": 2,
                        "unit": "hour",
                        "provenance": "explicit",
                    }
                ],
                "reminder_mode": "override",
            },
        },
        {
            "kind": "proposal",
            "language": "en",
            "operation": "propose_create_plan",
            "fields": {
                "title": "Weekly class",
                "schedule": {
                    "kind": "task",
                    "local_date": "2026-10-16",
                    "local_time": "10:00:00",
                    "timezone": "UTC",
                    "timing": "timed",
                },
                "recurrence": {
                    "frequency": "weekly",
                    "weekdays": [4],
                    "occurrence_count": 3,
                },
            },
        },
    )
    proposals = tuple(ActionProposal.model_validate_json(json.dumps(action)) for action in actions)
    return ProposalBatch(
        kind="proposal_batch", schema_version=1, language=SourceLanguage.EN, actions=proposals
    )


async def _ready_mixed_capture(
    sessions: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    now: datetime,
    authorization: ProcessingAuthorization,
) -> tuple[UUID, int, dict[str, object], str]:
    request_key = f"capture-model-outcome-{uuid4().hex}"

    def context_factory(draft: CaptureDraft, key: str, text: str) -> ConversationContext:
        return ConversationContext(
            owner_id=owner_id,
            request_id=key,
            language=SourceLanguage.EN,
            input_kind=InputKind.TEXT,
            text=text,
            reference_date=now.date(),
            reference_instant=now,
            timezone="UTC",
        )

    result: tuple[UUID, int, dict[str, object], str]
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        store = SqlAlchemyCaptureStore(uow.session)
        interpretation = await prepare_interpretation(
            store,
            owner_id=owner_id,
            request_key=request_key,
            text="Create one review and a weekly class",
            at=now,
            processing_grant_id=authorization.grant_id,
            processing_policy_version=authorization.policy_version,
            context_factory=context_factory,
        )
        assert isinstance(interpretation, CaptureInterpretation)
        preferences = await SqlAlchemyIdentityStore(uow.session).get_preferences(owner_id)
        assert preferences is not None
        reply = await finish_interpretation(
            store,
            interpretation,
            ModelSuccess(
                result=_mixed_proposal_batch(),
                provider_request_ids=(f"fake-provider-{uuid4().hex}",),
            ),
            preferences=preferences,
            at=now + timedelta(seconds=1),
        )
        assert reply.state == "ready" and reply.confirmation_credential
        draft = await store.get_draft(owner_id=owner_id, draft_id=reply.draft_id)
        assert draft is not None and draft.preview is not None
        result = (draft.id, draft.version, draft.preview, reply.confirmation_credential)
    return result


async def _confirm_capture(
    sessions: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    draft_id: UUID,
    version: int,
    credential: str,
    now: datetime,
    *,
    recurrence_store_factory: Callable[[AsyncSession], object] | None = None,
    application_name: str | None = None,
) -> CaptureTurnReply:
    reply: CaptureTurnReply
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        session = uow.session
        if application_name is not None:
            await session.execute(
                select(func.set_config("application_name", application_name, True))
            )
        reply = await confirm_ready_draft(
            SqlAlchemyCaptureStore(session),
            owner_id=owner_id,
            draft_id=draft_id,
            expected_version=version,
            credential=credential,
            plan_store=SqlAlchemyPlanningStore(session),
            recurrence_store=(
                cast(RecurrenceCommandStore, recurrence_store_factory(session))
                if recurrence_store_factory is not None
                else SqlAlchemySeriesStore(session)
            ),
            confirmation_verifier=SqlAlchemyCaptureConfirmationVerifier(session),
            recurrence_confirmation_verifier=SqlAlchemyCaptureRecurrenceConfirmationVerifier(
                session
            ),
            identity=SqlAlchemyIdentityStore(session),
            billing=SqlAlchemyAccessUsageStore(session),
            clock=FixedClock(now),
        )
    return reply


async def _wait_for_consent_blocker(
    sessions: async_sessionmaker[AsyncSession],
    *,
    waiter: str,
    blocker: str,
) -> None:
    async with sessions() as session:
        async with asyncio.timeout(10):
            while True:
                blocked = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiting "
                        "WHERE waiting.application_name = :waiter "
                        "AND waiting.wait_event_type = 'Lock' AND EXISTS ( "
                        "SELECT 1 FROM unnest(pg_blocking_pids(waiting.pid)) AS blocked(pid) "
                        "JOIN pg_stat_activity AS blocker ON blocker.pid = blocked.pid "
                        "WHERE blocker.application_name = :blocker))"
                    ),
                    {"waiter": waiter, "blocker": blocker},
                )
                if blocked:
                    return
                await session.rollback()
                await asyncio.sleep(0.01)


async def _wait_for_activity_or_confirmation_failure(
    sessions: async_sessionmaker[AsyncSession],
    *,
    application_name: str,
    confirmation: asyncio.Task,
) -> None:
    async with sessions() as session:
        async with asyncio.timeout(10):
            while True:
                if confirmation.done():
                    await confirmation
                    pytest.fail("confirmation completed before reaching its database barrier")
                waiting = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE application_name = :name AND wait_event_type = 'Lock' "
                        "AND wait_event = 'advisory')"
                    ),
                    {"name": application_name},
                )
                if waiting:
                    return
                await session.rollback()
                await asyncio.sleep(0.01)


def test_ai_preview_confirmation_serializes_before_withdrawal(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        revoke_engine = create_async_engine(capture_database)
        revoke_sessions = create_session_factory(revoke_engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        app_name = f"capture-confirm-{uuid4().hex[:12]}"
        revoke_name = f"capture-confirm-withdraw-{uuid4().hex[:12]}"
        barrier_key = uuid4().int & ((1 << 62) - 1)
        function_name = f"capture_confirm_wait_{uuid4().hex[:12]}"
        function, trigger = f"planning.{function_name}", function_name
        holder = None
        confirmation = withdrawal = None
        authorization = await _seed_capture_owner(
            sessions, owner_id, active_until=now + timedelta(days=8)
        )
        draft_id, _version, _preview, credential = await _ready_mixed_capture(
            sessions, owner_id, now, authorization
        )
        bot_id = abs(owner_id.int % 2_000_000_000) + 1
        telegram_user_id = abs(owner_id.int // 2 % 2_000_000_000) + 1
        update_id = uuid4().int % 9_000_000_000_000_000
        worker_engine = sqlalchemy_create_async_engine(
            capture_database.replace("postgresql://", "postgresql+psycopg://", 1),
            connect_args={"application_name": app_name},
        )
        worker_sessions = create_session_factory(worker_engine)
        try:
            async with sessions() as session, session.begin():
                session.add(
                    TelegramOwnerRecord(
                        bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id
                    )
                )
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload={
                        "update_id": update_id,
                        "callback_query": {
                            "id": f"capture-confirm-{uuid4().hex}",
                            "from": {
                                "id": telegram_user_id,
                                "is_bot": False,
                                "first_name": "Test",
                            },
                            "chat_instance": uuid4().hex,
                            "message": {
                                "message_id": update_id + 1,
                                "date": int(now.timestamp()),
                                "chat": {"id": telegram_user_id, "type": "private"},
                                "from": {
                                    "id": telegram_user_id,
                                    "is_bot": False,
                                    "first_name": "Test",
                                },
                                "text": "Review and confirm",
                            },
                            "data": capture_confirmation_callback_data(credential),
                        },
                    },
                    owner_resolver=PostgresTelegramOwnerResolver(),
                    received_at=now + timedelta(seconds=1),
                )
                assert receipt is not None and receipt.accepted
                admitted_job = await session.scalar(
                    select(Job).where(Job.deduplication_key == f"telegram:{bot_id}:{update_id}")
                )
                assert admitted_job is not None and admitted_job.owner_id == owner_id
                admitted_job.priority = 2_147_483_647
            claim = await claim_application_job(
                worker_sessions, worker_id=f"capture-confirm-{uuid4().hex[:10]}"
            )
            assert claim is not None and claim.owner_id == owner_id

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                        "BEGIN IF NEW.owner_id = '" + str(owner_id) + "'::uuid THEN "
                        f"PERFORM pg_advisory_xact_lock({barrier_key}); "
                        "END IF; RETURN NEW; END $$"
                    )
                )
                await connection.execute(
                    text(
                        f"CREATE TRIGGER {trigger} BEFORE INSERT ON planning.plan_items "
                        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
                    )
                )
            holder = await engine.connect()
            await holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": barrier_key})
            confirmation = asyncio.create_task(
                process_application_job(worker_sessions, claim, now=now + timedelta(seconds=2))
            )
            await _wait_for_activity_or_confirmation_failure(
                sessions, application_name=app_name, confirmation=confirmation
            )

            async def withdraw() -> None:
                async with revoke_sessions() as session, session.begin():
                    await session.execute(
                        select(func.set_config("application_name", revoke_name, True))
                    )
                    store = SqlAlchemyIdentityStore(session)
                    await store.lock_processing_consent(owner_id)
                    current = await get_processing_consent(store, owner_id=owner_id)
                    assert current.policy_version is not None
                    await revoke_processing_consent(
                        store,
                        owner_id=owner_id,
                        expected_revision=current.revision,
                        expected_grant_id=authorization.grant_id,
                        expected_policy_version=current.policy_version,
                        revoked_at=now + timedelta(seconds=3),
                    )

            withdrawal = asyncio.create_task(withdraw())
            await _wait_for_consent_blocker(revoke_sessions, waiter=revoke_name, blocker=app_name)
            assert not confirmation.done() and not withdrawal.done()
            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key})
            await holder.commit()
            await asyncio.wait_for(confirmation, timeout=10)
            await asyncio.wait_for(withdrawal, timeout=10)
            async with sessions() as session:
                consent = await get_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
                saved = await session.scalar(
                    select(func.count())
                    .select_from(PlanItemRecord)
                    .where(PlanItemRecord.owner_id == owner_id)
                )
                draft = await session.get(CaptureDraftRecord, draft_id)
                proofs = list(
                    (
                        await session.scalars(
                            select(CaptureConfirmationRecord).where(
                                CaptureConfirmationRecord.owner_id == owner_id
                            )
                        )
                    ).all()
                )
                events = list(
                    (
                        await session.scalars(
                            select(OutboxEvent).where(OutboxEvent.owner_id == owner_id)
                        )
                    ).all()
                )
                counts = await _capture_counts(session, owner_id)
            reply_events = [
                event for event in events if event.event_type == "telegram.reply.requested"
            ]
            projection_events = [
                event for event in events if event.event_type.startswith("planning.")
            ]
            assert consent.state == "revoked"
            assert saved == 2  # one-off plan plus the recurring series
            assert counts == (2, 4, 8, 1, 3, 6)
            assert len(projection_events) == 5
            assert len(reply_events) == 1
            assert (
                reply_events[0].payload["payload"]["processing_classification"] == "ai_derived"
            )
            assert reply_events[0].payload["payload"]["source_job_id"] == str(claim.job_id)
            assert draft is not None and draft.state == "confirmed"
            assert proofs and all(proof.consumed_at is not None for proof in proofs)
        finally:
            for task in (confirmation, withdrawal):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (confirmation, withdrawal) if task is not None),
                return_exceptions=True,
            )
            if holder is not None:
                try:
                    await holder.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": barrier_key}
                    )
                    await holder.commit()
                except Exception:
                    pass
                await holder.close()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(f"DROP TRIGGER IF EXISTS {trigger} ON planning.plan_items")
                    )
                    await connection.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
            finally:
                await engine.dispose()
                await worker_engine.dispose()
                await revoke_engine.dispose()

    run_local_async(scenario)



def test_withdrawal_before_ai_preview_confirmation_rejects_old_proof_after_regrant(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        authorization = await _seed_capture_owner(
            sessions, owner_id, active_until=now + timedelta(days=8)
        )
        draft_id, _version, _preview, credential = await _ready_mixed_capture(
            sessions, owner_id, now, authorization
        )
        bot_id = abs(owner_id.int % 2_000_000_000) + 1
        telegram_user_id = abs(owner_id.int // 2 % 2_000_000_000) + 1
        try:
            async with sessions() as session, session.begin():
                session.add(
                    TelegramOwnerRecord(
                        bot_id=bot_id, telegram_user_id=telegram_user_id, owner_id=owner_id
                    )
                )

            async with sessions() as session, session.begin():
                store = SqlAlchemyIdentityStore(session)
                current = await get_processing_consent(store, owner_id=owner_id)
                assert current.policy_version is not None
                await revoke_processing_consent(
                    store,
                    owner_id=owner_id,
                    expected_revision=current.revision,
                    expected_grant_id=authorization.grant_id,
                    expected_policy_version=current.policy_version,
                    revoked_at=now + timedelta(seconds=1),
                )

            async def admit_and_process(sequence: int) -> UUID:
                callback_at = now + timedelta(seconds=sequence * 3)
                update_id = uuid4().int % 9_000_000_000_000_000
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload={
                            "update_id": update_id,
                            "callback_query": {
                                "id": f"capture-withdraw-{sequence}-{uuid4().hex}",
                                "from": {
                                    "id": telegram_user_id,
                                    "is_bot": False,
                                    "first_name": "Test",
                                },
                                "chat_instance": uuid4().hex,
                                "message": {
                                    "message_id": update_id + 1,
                                    "date": int(callback_at.timestamp()),
                                    "chat": {"id": telegram_user_id, "type": "private"},
                                    "from": {
                                        "id": telegram_user_id,
                                        "is_bot": False,
                                        "first_name": "Test",
                                    },
                                    "text": "Review and confirm",
                                },
                                "data": capture_confirmation_callback_data(credential),
                            },
                        },
                        owner_resolver=PostgresTelegramOwnerResolver(),
                        received_at=callback_at,
                    )
                    assert receipt is not None and receipt.accepted
                    job = await session.scalar(
                        select(Job).where(
                            Job.deduplication_key == f"telegram:{bot_id}:{update_id}"
                        )
                    )
                    assert job is not None and job.owner_id == owner_id
                    if sequence == 1:
                        assert job.processing_grant_id is None
                    else:
                        assert job.processing_grant_id == regrant.grant_id
                    job.priority = 2_147_483_647

                claim = await claim_application_job(
                    sessions, worker_id=f"capture-withdraw-{sequence}-{uuid4().hex[:8]}"
                )
                assert claim is not None and claim.owner_id == owner_id
                # The callback is handled without AI; its reply retains the saved
                # capture response classification from the earlier provider call.
                await process_application_job(
                    sessions, claim, now=callback_at + timedelta(seconds=1), assistant=None
                )
                return claim.job_id

            async def assert_denied_callback(job_id: UUID, expected_count: int) -> None:
                async with sessions() as session:
                    job = await session.get(Job, job_id)
                    assert job is not None and job.status == "succeeded"
                    events = list(
                        (
                            await session.scalars(
                                select(OutboxEvent).where(
                                    OutboxEvent.owner_id == owner_id,
                                    OutboxEvent.event_type == "telegram.reply.requested",
                                )
                            )
                        ).all()
                    )
                    reply = next(
                        (
                            event
                            for event in events
                            if event.payload.get("payload", {}).get("source_job_id") == str(job_id)
                        ),
                        None,
                    )
                    assert reply is not None
                    assert reply.payload["payload"]["plain_text"] == (
                        "Plan request\n• Allow processing in /privacy before sending this task. "
                        "Then send it again."
                    )
                    assert reply.payload["payload"]["processing_classification"] == "deterministic"
                    counts = await _capture_counts(session, owner_id)
                    draft = await session.get(CaptureDraftRecord, draft_id)
                    proofs = list(
                        (
                            await session.scalars(
                                select(CaptureConfirmationRecord).where(
                                    CaptureConfirmationRecord.owner_id == owner_id
                                )
                            )
                        ).all()
                    )
                    provider_calls = await session.scalar(
                        select(func.count())
                        .select_from(ProviderCallRecord)
                        .where(ProviderCallRecord.owner_id == owner_id)
                    )
                assert counts[:5] == (0, 0, 0, 0, 0)
                assert counts[5] == expected_count
                assert draft is not None and draft.state == "ready"
                assert proofs and all(proof.consumed_at is None for proof in proofs)
                assert provider_calls == 0

            first_job_id = await admit_and_process(1)
            await assert_denied_callback(first_job_id, expected_count=1)

            async with sessions() as session, session.begin():
                regrant = await seed_accepted_processing_consent(
                    session,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    telegram_user_id=telegram_user_id,
                    expected_revision=2,
                )
            assert regrant.grant_id != authorization.grant_id
            async with sessions() as session:
                identity = SqlAlchemyIdentityStore(session)
                assert await authorize_processing_consent(
                    identity, owner_id=owner_id, authorization=regrant
                )
                assert not await authorize_processing_consent(
                    identity, owner_id=owner_id, authorization=authorization
                )

            second_job_id = await admit_and_process(2)
            await assert_denied_callback(second_job_id, expected_count=2)
        finally:
            await engine.dispose()

    run_local_async(scenario)


async def _capture_counts(session: AsyncSession, owner_id: UUID) -> tuple[int, ...]:
    models = (
        PlanItemRecord,
        OccurrenceRecord,
        ReminderRuleRecord,
        SeriesRecord,
        SeriesOccurrenceRecord,
        OutboxEvent,
    )
    counts = []
    for model in models:
        counts.append(
            await session.scalar(
                select(func.count()).select_from(model).where(model.owner_id == owner_id)
            )
        )
    return tuple(int(count or 0) for count in counts)


def _schedule_keys(rows: list[dict[str, object]]) -> set[tuple[str, int, str, str]]:
    return {
        (
            str(row["offset_kind"]),
            cast(int, row["amount"]),
            str(row["source"]),
            str(row["due_at"]),
        )
        for row in rows
    }


def test_confirm_mixed_batch_restarts_replays_once_and_matches_reconciled_intents(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        authorization = await _seed_capture_owner(
            sessions, owner_id, active_until=now + timedelta(days=8)
        )
        draft_id, version, preview, first_credential = await _ready_mixed_capture(
            sessions, owner_id, now, authorization
        )
        async with sessions() as session:
            assert await _capture_counts(session, owner_id) == (0, 0, 0, 0, 0, 0)
        await engine.dispose()

        # A fresh engine/session simulates process restart. The durable preview is
        # restored and receives a new ephemeral callback credential.
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        async with SqlAlchemyUnitOfWork(sessions) as uow:
            restored = await restore_ready_preview(
                SqlAlchemyCaptureStore(uow.session),
                owner_id=owner_id,
                draft_id=draft_id,
                at=now + timedelta(seconds=2),
            )
        assert restored.version == version and restored.confirmation_credential
        assert restored.response["preview"] == preview

        results = await asyncio.gather(
            _confirm_capture(
                sessions,
                owner_id,
                draft_id,
                version,
                restored.confirmation_credential,
                now + timedelta(seconds=3),
            ),
            _confirm_capture(
                sessions,
                owner_id,
                draft_id,
                version,
                restored.confirmation_credential,
                now + timedelta(seconds=3),
            ),
        )
        assert results[0] == results[1]
        assert results[0].state == "confirmed"
        confirmed_result = cast(dict[str, object], results[0].response["result"])
        result_items = cast(list[dict[str, object]], confirmed_result["items"])
        assert len(result_items) == 2
        one_off_plans = cast(list[dict[str, object]], result_items[0]["plans"])
        one_off_result = one_off_plans[0]
        series_result = result_items[1]

        preview_items = cast(list[dict[str, object]], preview["items"])
        one_off_preview = next(item for item in preview_items if item["kind"] == "plan")
        recurring_preview = next(item for item in preview_items if item["kind"] == "recurring")
        confirmed_occurrences = cast(list[dict[str, object]], series_result["occurrences"])
        occurrence_ids = [UUID(cast(str, one_off_result["occurrence_id"]))] + [
            UUID(cast(str, item["occurrence_id"])) for item in confirmed_occurrences
        ]
        async with sessions() as session:
            plans = list(
                (
                    await session.scalars(
                        select(PlanItemRecord).where(PlanItemRecord.owner_id == owner_id)
                    )
                ).all()
            )
            occurrences = list(
                (
                    await session.scalars(
                        select(OccurrenceRecord)
                        .where(OccurrenceRecord.owner_id == owner_id)
                        .order_by(OccurrenceRecord.local_date, OccurrenceRecord.local_time)
                    )
                ).all()
            )
            rules = list(
                (
                    await session.scalars(
                        select(ReminderRuleRecord)
                        .where(ReminderRuleRecord.owner_id == owner_id)
                        .order_by(ReminderRuleRecord.occurrence_id, ReminderRuleRecord.offset_kind)
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(OutboxEvent.owner_id == owner_id)
                    )
                ).all()
            )
        assert len(plans) == 2 and len(occurrences) == 4
        assert len(occurrence_ids) == 4
        assert {event.event_type for event in events} == {
            "planning.plan_created",
            "planning.series_occurrence_created",
            "planning.series_created",
        }
        assert len(events) == 5
        assert len(rules) == 8

        def rule_keys_for(occurrence_id: UUID) -> set[tuple[str, int, str, str]]:
            return {
                (
                    row.offset_kind,
                    row.offset_amount,
                    row.source,
                    row.due_at.astimezone(UTC).isoformat(),
                )
                for row in rules
                if row.occurrence_id == occurrence_id
            }

        one_off_details = cast(dict[str, object], one_off_preview["details"])
        assert rule_keys_for(occurrence_ids[0]) == _schedule_keys(
            cast(list[dict[str, object]], one_off_details["reminders"])
        )
        recurring_details = cast(dict[str, object], recurring_preview["details"])
        recurring_rows = cast(list[dict[str, object]], recurring_details["occurrences"])
        for series_item, preview_item in zip(confirmed_occurrences, recurring_rows, strict=True):
            assert rule_keys_for(UUID(cast(str, series_item["occurrence_id"]))) == _schedule_keys(
                cast(list[dict[str, object]], preview_item["reminders"])
            )

        from pin.adapters.telegram.application_worker import process_pending_reminder_schedules

        assert await process_pending_reminder_schedules(sessions, now=now, limit=1000) == 5
        async with sessions() as session:
            intents = list(
                (
                    await session.scalars(
                        select(ReminderIntentRecord)
                        .where(
                            ReminderIntentRecord.owner_id == owner_id,
                            ReminderIntentRecord.state == "scheduled",
                        )
                        .order_by(
                            ReminderIntentRecord.occurrence_id,
                            ReminderIntentRecord.offset_kind,
                        )
                    )
                ).all()
            )
            receipts = list(
                (
                    await session.scalars(
                        select(NotificationReconciliationReceiptRecord).where(
                            NotificationReconciliationReceiptRecord.owner_id == owner_id
                        )
                    )
                ).all()
            )
            assert await _capture_counts(session, owner_id) == (2, 4, 8, 1, 3, 5)
        assert len(intents) == 8
        assert len(receipts) == 5
        assert {
            (
                row.occurrence_id,
                row.offset_kind,
                row.offset_amount,
                row.source,
                row.due_at.astimezone(UTC).isoformat(),
            )
            for row in intents
        } == {
            (
                row.occurrence_id,
                row.offset_kind,
                row.offset_amount,
                row.source,
                row.due_at.astimezone(UTC).isoformat(),
            )
            for row in rules
        }

        await engine.dispose()
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        replay = await _confirm_capture(
            sessions,
            owner_id,
            draft_id,
            version,
            first_credential,
            now + timedelta(seconds=4),
        )
        assert replay == results[0]
        async with sessions() as session:
            assert await _capture_counts(session, owner_id) == (2, 4, 8, 1, 3, 5)
        await engine.dispose()

    run_local_async(scenario)


def test_confirmation_rechecks_owner_draft_version_expiry_preferences_and_access(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        cases = ("wrong_owner", "stale_version", "expired", "changed_preferences", "access_expired")
        for case in cases:
            owner_id = uuid4()
            authorization = await _seed_capture_owner(
                sessions, owner_id, active_until=now + timedelta(days=8)
            )
            draft_id, version, _preview, credential = await _ready_mixed_capture(
                sessions, owner_id, now, authorization
            )
            confirm_owner = owner_id
            confirm_version = version
            confirm_at = now + timedelta(seconds=2)
            expected: type[BaseException]
            if case == "wrong_owner":
                confirm_owner = uuid4()
                expected = CaptureConflict
            elif case == "stale_version":
                confirm_version += 1
                expected = CaptureConflict
            elif case == "expired":
                confirm_at = now + timedelta(hours=24, seconds=1)
                expected = ConfirmationRejected
            elif case == "changed_preferences":
                async with sessions() as session, session.begin():
                    profile = await session.get(PreferencesRecord, owner_id)
                    assert profile is not None
                    profile.version = 2
                    profile.reminder_elapsed_hours = 2
                expected = CaptureConflict
            else:
                async with sessions() as session, session.begin():
                    period = await session.scalar(
                        select(AccessPeriodRecord).where(AccessPeriodRecord.owner_id == owner_id)
                    )
                    assert period is not None
                    period.ends_at = confirm_at
                expected = AccessDenied

            with pytest.raises(expected):
                await _confirm_capture(
                    sessions,
                    confirm_owner,
                    draft_id,
                    confirm_version,
                    credential,
                    confirm_at,
                )
            async with sessions() as session:
                assert await _capture_counts(session, owner_id) == (0, 0, 0, 0, 0, 0)
                draft = await SqlAlchemyCaptureStore(session).get_draft(
                    owner_id=owner_id, draft_id=draft_id
                )
                assert draft is not None and draft.state.value == "ready"
                proofs = list(
                    (
                        await session.scalars(
                            select(CaptureConfirmationRecord).where(
                                CaptureConfirmationRecord.owner_id == owner_id,
                                CaptureConfirmationRecord.draft_id == draft_id,
                            )
                        )
                    ).all()
                )
                assert proofs and all(proof.consumed_at is None for proof in proofs)
        await engine.dispose()

    run_local_async(scenario)


def test_mixed_batch_failure_rolls_back_plans_series_proofs_and_outbox(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        authorization = await _seed_capture_owner(
            sessions, owner_id, active_until=now + timedelta(days=8)
        )
        draft_id, version, _preview, credential = await _ready_mixed_capture(
            sessions, owner_id, now, authorization
        )
        with pytest.raises(RuntimeError, match="failure after recurring schedule"):
            await _confirm_capture(
                sessions,
                owner_id,
                draft_id,
                version,
                credential,
                now + timedelta(seconds=2),
                recurrence_store_factory=lambda session: FailAfterSeriesPersistStore(
                    SqlAlchemySeriesStore(session)
                ),
            )
        async with sessions() as session:
            assert await _capture_counts(session, owner_id) == (0, 0, 0, 0, 0, 0)
            draft = await SqlAlchemyCaptureStore(session).get_draft(
                owner_id=owner_id, draft_id=draft_id
            )
            assert draft is not None and draft.state.value == "ready"
            proofs = list(
                (
                    await session.scalars(
                        select(CaptureConfirmationRecord).where(
                            CaptureConfirmationRecord.owner_id == owner_id,
                            CaptureConfirmationRecord.draft_id == draft_id,
                        )
                    )
                ).all()
            )
            assert proofs and all(proof.consumed_at is None for proof in proofs)
        await engine.dispose()

    run_local_async(scenario)


def test_truncated_model_failure_is_a_durable_clarification_with_no_plan_rows(
    capture_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(capture_database)
        sessions = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime(2026, 10, 14, 8, tzinfo=UTC)
        authorization = await _seed_capture_owner(
            sessions, owner_id, active_until=now + timedelta(days=8)
        )
        request_key = f"capture-truncated-{uuid4().hex}"

        def context_factory(draft: CaptureDraft, key: str, text: str) -> ConversationContext:
            return ConversationContext(
                owner_id=owner_id,
                request_id=key,
                language=SourceLanguage.EN,
                input_kind=InputKind.TEXT,
                text=text,
                reference_date=now.date(),
                reference_instant=now,
                timezone="UTC",
            )

        async with SqlAlchemyUnitOfWork(sessions) as uow:
            store = SqlAlchemyCaptureStore(uow.session)
            interpretation = await prepare_interpretation(
                store,
                owner_id=owner_id,
                request_key=request_key,
                text="Create ten plans",
                at=now,
                processing_grant_id=authorization.grant_id,
                processing_policy_version=authorization.policy_version,
                context_factory=context_factory,
            )
            assert isinstance(interpretation, CaptureInterpretation)
            reply = await finish_interpretation(
                store,
                interpretation,
                ModelFailure(
                    category=FailureCategory.INVALID_OUTPUT,
                    provider_request_id="fake-truncated-response",
                    usage_recorded=True,
                ),
                preferences=PreferencesSnapshot(
                    owner_id=owner_id,
                    language="en",
                    home_zone="UTC",
                    reminder_calendar_days=1,
                    reminder_elapsed_hours=1,
                    quiet_start=time(22),
                    quiet_end=time(8),
                    review_time=time(18),
                    version=1,
                ),
                at=now + timedelta(seconds=1),
            )
        assert reply.state == "collecting"
        assert reply.response["kind"] == "reply"
        assert not any(
            word in str(reply.response["text"]).casefold()
            for word in ("saved", "created", "confirmed")
        )
        async with sessions() as session:
            assert await _capture_counts(session, owner_id) == (0, 0, 0, 0, 0, 0)
            draft = await SqlAlchemyCaptureStore(session).get_draft(
                owner_id=owner_id, draft_id=reply.draft_id
            )
            assert draft is not None and draft.state.value == "collecting"
            assert draft.preview is None and draft.normalized_actions == ()
        await engine.dispose()

    run_local_async(scenario)
