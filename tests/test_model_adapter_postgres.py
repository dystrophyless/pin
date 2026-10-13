from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pin.contexts.conversation.application.contracts import (
    CallStart,
    CallUsage,
    ReservationStatus,
)
from pin.contexts.conversation.application.dto import (
    ConversationContext,
    ModelFailure,
    ModelSuccess,
    Reply,
)
from pin.contexts.conversation.application.service import ROUTE, BoundedPlanningAssistant
from pin.contexts.conversation.domain.proposals import InputKind, SourceLanguage
from pin.contexts.conversation.infrastructure.models import (
    ConversationTurnResultRecord,
    ProviderCallRecord,
)
from pin.contexts.conversation.infrastructure.usage import (
    ProviderReceiptConflict,
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.persistence.async_runner import run_local_async
from pin.platform.jobs import Job, JobClaim, JobLane, LeaseLost, claim_job, enqueue_job

DB_URL = os.environ.get("PIN_MODEL_TEST_DATABASE_URL")


class _FakeTransport:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.request_key = uuid4().hex[:12]

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        assert payload["model"] == ROUTE
        return {
            "id": f"gen-pg-{self.request_key}-{self.calls}",
            "model": ROUTE,
            "choices": [{"message": {"content": self.content}}],
            "usage": {
                "prompt_tokens": 64,
                "completion_tokens": 20,
                "total_tokens": 84,
                "cost": "0.00000452",
            },
        }


class _Allowance:
    def __init__(self, *, replay: bool = False, fail_settle_once: bool = False) -> None:
        self.replay = replay
        self.fail_settle_once = fail_settle_once
        self.settlements = 0
        self.refunds = 0

    async def reserve(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> ReservationStatus:
        del owner_id, request_id, at, claim
        return ReservationStatus.REPLAY if self.replay else ReservationStatus.RESERVED

    async def settle(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        del owner_id, request_id, at, claim
        if self.fail_settle_once:
            self.fail_settle_once = False
            raise RuntimeError("simulated crash after result receipt commit")
        self.settlements += 1

    async def refund(
        self, *, owner_id: UUID, request_id: str, at: datetime, claim: object | None = None
    ) -> None:
        del owner_id, request_id, at, claim
        self.refunds += 1


def _db_url() -> str:
    if not DB_URL:
        pytest.skip("set PIN_MODEL_TEST_DATABASE_URL to the verified disposable PostgreSQL DB")
    return DB_URL


def _context(request_id: str) -> ConversationContext:
    return ConversationContext(
        owner_id=UUID("8b1fda60-12e5-4cc7-808c-9bfe605abf99"),
        request_id=request_id,
        language=SourceLanguage.RU,
        input_kind=InputKind.TEXT,
        text="Создай задачу на завтра.",
        reference_date=date(2026, 10, 13),
        timezone="Asia/Qyzylorda",
    )


async def _run_postgres_usage_receipts_are_idempotent_and_unknown_is_not_zero() -> None:
    engine = create_async_engine(_db_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    recorder = SqlAlchemyProviderUsageRecorder(factory)
    owner = uuid4()
    turn = f"usage-{uuid4()}"
    first = CallStart(owner, turn, uuid4(), 1, ROUTE, datetime.now(UTC))
    provider_id = f"gen-dedup-{uuid4()}"
    receipt = CallUsage(provider_id, ROUTE, 10, 5, 15, Decimal("0.000001"), 4)
    try:
        await recorder.start_call(first)
        await recorder.record_usage(first, receipt, result_category="invalid_output")
        await recorder.record_usage(first, receipt, result_category="invalid_output")

        duplicate = CallStart(owner, turn, uuid4(), 2, ROUTE, datetime.now(UTC))
        await recorder.start_call(duplicate)
        await recorder.record_usage(duplicate, receipt, result_category="valid")

        unknown = CallStart(owner, turn, uuid4(), 3, ROUTE, datetime.now(UTC))
        await recorder.start_call(unknown)
        await recorder.mark_unknown(unknown, result_category="timeout")

        partial = CallStart(owner, turn, uuid4(), 4, ROUTE, datetime.now(UTC))
        partial_id = f"gen-partial-{uuid4()}"
        await recorder.start_call(partial)
        await recorder.record_usage(
            partial,
            CallUsage(partial_id, ROUTE, 17, None, None, Decimal("0.000002"), 8),
            result_category="partial_usage",
        )

        malformed = CallStart(owner, turn, uuid4(), 1, ROUTE, datetime.now(UTC))
        malformed_id = f"gen-malformed-{uuid4()}"
        await recorder.start_call(malformed)
        await recorder.record_usage(
            malformed,
            CallUsage(malformed_id, ROUTE, 12, 4, 16, Decimal("0.000003"), 9),
            result_category="invalid_response",
        )

        async with factory() as session:
            rows = (
                await session.scalars(
                    select(ProviderCallRecord).where(
                        ProviderCallRecord.owner_id == owner,
                        ProviderCallRecord.turn_id == turn,
                    )
                )
            ).all()
            assert len(rows) == 5
            states = {row.call_id: row for row in rows}
            assert states[first.call_id].state == "reported"
            assert states[first.call_id].cost_usd == Decimal("0.000001000000")
            assert states[duplicate.call_id].state == "deduplicated"
            assert states[duplicate.call_id].duplicate_of_call_id == first.call_id
            assert states[unknown.call_id].state == "unknown"
            assert states[unknown.call_id].cost_usd is None
            assert states[unknown.call_id].prompt_tokens is None
            assert states[partial.call_id].state == "unknown"
            assert states[partial.call_id].provider_request_id == partial_id
            assert states[partial.call_id].prompt_tokens == 17
            assert states[partial.call_id].completion_tokens is None
            assert states[partial.call_id].cost_usd == Decimal("0.000002000000")
            assert states[malformed.call_id].state == "reported"
            assert states[malformed.call_id].provider_request_id == malformed_id
            assert states[malformed.call_id].cost_usd == Decimal("0.000003000000")

        with pytest.raises(ProviderReceiptConflict):
            await recorder.record_usage(
                first,
                CallUsage("gen-dedup", ROUTE, 11, 5, 16, Decimal("0.000001"), 4),
                result_category="invalid_output",
            )
    finally:
        await engine.dispose()


async def _run_postgres_replay_recovers_valid_result_after_settlement_crash() -> None:
    engine = create_async_engine(_db_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    recorder = SqlAlchemyProviderUsageRecorder(factory)
    request_id = f"replay-{uuid4()}"
    context = _context(request_id)
    provider = _FakeTransport(
        json.dumps(
            {
                "kind": "reply",
                "language": "ru",
                "text": (
                    "\u041d\u0443\u0436\u043d\u043e "
                    "\u0443\u0442\u043e\u0447\u043d\u0438\u0442\u044c "
                    "\u0441\u0440\u043e\u043a?"
                ),
            }
        )
    )
    first_allowance = _Allowance(fail_settle_once=True)
    first = BoundedPlanningAssistant(
        transport=provider, usage=recorder, allowance=first_allowance, now=lambda: datetime.now(UTC)
    )
    replay_allowance = _Allowance(replay=True)
    replay = BoundedPlanningAssistant(
        transport=provider,
        usage=recorder,
        allowance=replay_allowance,
        now=lambda: datetime.now(UTC),
    )
    try:
        interrupted = await first.interpret(context, at=datetime.now(UTC))
        assert isinstance(interrupted, ModelFailure)
        assert interrupted.usage_recorded
        assert first_allowance.refunds == 0
        async with factory() as session:
            saved = await session.scalar(
                select(ConversationTurnResultRecord).where(
                    ConversationTurnResultRecord.owner_id == context.owner_id,
                    ConversationTurnResultRecord.turn_id == request_id,
                )
            )
            assert saved is not None
            assert saved.result["kind"] == "reply"
        recovered = await replay.interpret(context, at=datetime.now(UTC))
        assert isinstance(recovered, ModelSuccess)
        assert isinstance(recovered.result, Reply)
        assert recovered.result.text == (
            "\u041d\u0443\u0436\u043d\u043e "
            "\u0443\u0442\u043e\u0447\u043d\u0438\u0442\u044c "
            "\u0441\u0440\u043e\u043a?"
        )
        assert recovered.provider_request_ids == (f"gen-pg-{provider.request_key}-1",)
        assert replay_allowance.settlements == 1
        assert provider.calls == 1
    finally:
        await engine.dispose()


async def _run_postgres_stale_job_claim_cannot_write_provider_cost_or_result() -> None:
    engine = create_async_engine(_db_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    recorder = SqlAlchemyProviderUsageRecorder(factory)
    claim = JobClaim(
        job_id=uuid4(),
        lane=JobLane.APPLICATION,
        kind="conversation.interpret",
        payload_version=1,
        payload={},
        owner_id=uuid4(),
        conversation_sequence=1,
        attempt=1,
        worker_id="test-worker",
        token=uuid4(),
        fencing_generation=1,
        lease_until=datetime.now(UTC),
    )
    call = CallStart(uuid4(), f"stale-{uuid4()}", uuid4(), 1, ROUTE, datetime.now(UTC))
    try:
        with pytest.raises(LeaseLost):
            await recorder.start_call(call, claim=claim)
        async with factory() as session:
            count = await session.scalar(
                select(ProviderCallRecord.call_id).where(
                    ProviderCallRecord.owner_id == call.owner_id,
                    ProviderCallRecord.turn_id == call.turn_id,
                )
            )
            assert count is None
    finally:
        await engine.dispose()


def test_postgres_usage_receipts_are_idempotent_and_unknown_is_not_zero() -> None:
    run_local_async(_run_postgres_usage_receipts_are_idempotent_and_unknown_is_not_zero)


def test_postgres_replay_recovers_valid_result_after_settlement_crash() -> None:
    run_local_async(_run_postgres_replay_recovers_valid_result_after_settlement_crash)


def test_postgres_stale_job_claim_cannot_write_provider_cost_or_result() -> None:
    run_local_async(_run_postgres_stale_job_claim_cannot_write_provider_cost_or_result)


async def _run_postgres_stale_response_records_cost_without_settlement() -> None:
    engine = create_async_engine(_db_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = uuid4()
    now = datetime.now(UTC)
    request_id = f"late-{uuid4()}"
    period_end = now + timedelta(days=7)
    context = _context(request_id).model_copy(update={"owner_id": owner})

    class ExpireClaimTransport:
        async def complete(self, payload: dict[str, object]) -> dict[str, object]:
            assert payload["model"] == ROUTE
            async with factory() as session, session.begin():
                await session.execute(
                    update(Job)
                    .where(Job.id == claim.job_id)
                    .values(lease_until=func.clock_timestamp() - text("interval '1 second'"))
                )
            return {
                "id": f"gen-late-{request_id}",
                "model": ROUTE,
                "choices": [
                    {
                        "message": {
                            "content": '{"kind":"reply","language":"ru","text":"Готово"}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 16,
                    "completion_tokens": 4,
                    "total_tokens": 20,
                    "cost": "0.000001",
                },
            }

    try:
        async with factory() as session, session.begin():
            await session.execute(
                text("INSERT INTO billing.access_accounts (owner_id) VALUES (:owner)"),
                {"owner": owner},
            )
            await session.execute(
                text(
                    "INSERT INTO billing.access_periods "
                    "(owner_id, period_id, policy_kind, policy_version, starts_at, ends_at, "
                    "ai_turn_limit, voice_second_limit) "
                    "VALUES (:owner, 'model-test', 'trial', 'v1', :starts, :ends, 15, 900)"
                ),
                {"owner": owner, "starts": now - timedelta(minutes=1), "ends": period_end},
            )
            await enqueue_job(
                session,
                lane=JobLane.APPLICATION,
                kind="conversation.interpret",
                payload={},
                deduplication_key=f"model-test:{request_id}",
                owner_id=owner,
            )
        async with factory() as session, session.begin():
            claimed = await claim_job(
                session,
                lane=JobLane.APPLICATION,
                worker_id="model-test-worker",
                lease_for=timedelta(minutes=5),
            )
            assert claimed is not None
            claim = claimed

        recorder = SqlAlchemyProviderUsageRecorder(factory)
        allowance = SqlAlchemyTurnAllowance(factory)
        assistant = BoundedPlanningAssistant(
            transport=ExpireClaimTransport(),
            usage=recorder,
            allowance=allowance,
            now=lambda: datetime.now(UTC),
        )
        outcome = await assistant.interpret(context, at=now, claim=claim)
        assert isinstance(outcome, ModelFailure)
        assert outcome.category.value == "stale_job"
        assert outcome.usage_recorded
        assert (
            await allowance.reserve(
                owner_id=owner,
                request_id=request_id,
                at=now + timedelta(seconds=1),
            )
            is ReservationStatus.REPLAY
        )
        async with factory() as session:
            provider_row = await session.scalar(
                select(ProviderCallRecord).where(
                    ProviderCallRecord.owner_id == owner,
                    ProviderCallRecord.turn_id == request_id,
                )
            )
            reservation = await session.execute(
                text(
                    "SELECT state, settled_ai_turns FROM billing.usage_reservations "
                    "WHERE owner_id = :owner AND request_id = :request"
                ),
                {"owner": owner, "request": request_id},
            )
            reservation_row = reservation.one()
            result = await session.scalar(
                select(ConversationTurnResultRecord).where(
                    ConversationTurnResultRecord.owner_id == owner,
                    ConversationTurnResultRecord.turn_id == request_id,
                )
            )
            assert provider_row is not None
            assert provider_row.state == "reported"
            assert provider_row.provider_request_id == f"gen-late-{request_id}"
            assert provider_row.cost_usd == Decimal("0.000001000000")
            assert reservation_row.state == "reserved"
            assert reservation_row.settled_ai_turns == 0
            assert result is None
    finally:
        await engine.dispose()


def test_postgres_stale_response_records_cost_without_settlement() -> None:
    run_local_async(_run_postgres_stale_response_records_cost_without_settlement)
