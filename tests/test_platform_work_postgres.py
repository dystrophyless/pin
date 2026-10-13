from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update

from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.models import ActionReceipt
from pin.platform.contracts import EventEnvelope
from pin.platform.events import (
    ConsumeOutcome,
    add_outbox_event,
    consume_event,
    mark_outbox_published,
)
from pin.platform.jobs import (
    JobLane,
    LeaseLost,
    claim_job,
    complete_job,
    enqueue_job,
    fail_job,
    lock_job_for_effects,
    receive_update,
    record_job_effect_once,
    renew_job_lease,
)
from pin.platform.models import (
    ConversationLease,
    InboxUpdate,
    Job,
    OutboxConsumption,
    OutboxEvent,
)

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def platform_database() -> Iterator[str]:
    database_url = os.getenv("PIN_TEST_PLATFORM_DATABASE_URL")
    if not database_url:
        pytest.skip("set PIN_TEST_PLATFORM_DATABASE_URL to the dedicated platform test database")
    command.upgrade(_alembic_config(database_url), "head")
    yield database_url


def test_duplicate_inbox_receipt_creates_one_ordered_job(platform_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, update_id, bot_id = uuid4(), uuid4().int % (2**62) + 1, 712345678
        try:

            async def receive(text_value: str):
                async with sessions() as session, session.begin():
                    return await receive_update(
                        session,
                        bot_id=bot_id,
                        update_id=update_id,
                        owner_id=owner,
                        update_type="message",
                        payload={"text": text_value},
                    )

            results = await asyncio.gather(receive("first"), receive("duplicate"))
            accepted = [result for result in results if result.accepted]
            assert len(accepted) == 1
            first = accepted[0]
            assert all(result.inbox_id == first.inbox_id for result in results)
            assert all(result.job_id == first.job_id for result in results)
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(InboxUpdate)
                        .where(InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id)
                    )
                    == 1
                )
                job = await session.get(Job, first.job_id)
                assert job is not None and job.conversation_sequence == 1
            async with sessions() as session, session.begin():
                claim = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="inbox-test-cleanup",
                    lease_for=timedelta(seconds=30),
                )
                assert claim is not None and claim.job_id == first.job_id
                await complete_job(session, claim)
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_concurrent_claimers_cannot_claim_the_same_job(platform_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                job = await enqueue_job(
                    session,
                    lane=JobLane.DELIVERY,
                    kind="notification:probe",
                    payload={"probe": True},
                    deduplication_key=f"claim-race:{uuid4()}",
                )
                job_id = job.id

            async def compete(worker: int):
                async with sessions() as session, session.begin():
                    return await claim_job(
                        session,
                        lane=JobLane.DELIVERY,
                        worker_id=f"claim-race-{worker}",
                        lease_for=timedelta(seconds=30),
                    )

            claims = await asyncio.gather(*(compete(worker) for worker in range(8)))
            winners = [claim for claim in claims if claim is not None]
            assert len(winners) == 1
            assert winners[0].job_id == job_id
            async with sessions() as session, session.begin():
                await complete_job(session, winners[0])
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_expired_lease_reclaim_fences_old_business_and_event_writes(
    platform_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, aggregate, key = uuid4(), uuid4(), f"fence:{uuid4()}"
        stale_followup_key = f"stale-follow-up:{uuid4()}"
        event = EventEnvelope(
            id=uuid4(),
            event_type="work.effect",
            schema_version=1,
            aggregate_id=aggregate,
            aggregate_version=1,
            owner_id=owner,
            occurred_at=datetime.now(UTC),
            payload={"effect": "should-not-appear"},
        )
        try:
            async with sessions() as session, session.begin():
                await add_outbox_event(session, event)
                await enqueue_job(
                    session,
                    lane=JobLane.APPLICATION,
                    kind="conversation:message",
                    payload={},
                    deduplication_key=key,
                    owner_id=owner,
                    ordered_conversation=True,
                )
            async with sessions() as session, session.begin():
                first = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="worker-a",
                    lease_for=timedelta(seconds=30),
                )
                assert first is not None
                renewed_until = await renew_job_lease(
                    session, first, lease_for=timedelta(minutes=2)
                )
                assert renewed_until > first.lease_until

            async with sessions() as session, session.begin():
                await session.execute(
                    update(Job)
                    .where(Job.id == first.job_id)
                    .values(lease_until=text("clock_timestamp() - interval '1 second'"))
                )
                await session.execute(
                    update(ConversationLease)
                    .where(ConversationLease.owner_id == owner)
                    .values(lease_until=text("clock_timestamp() - interval '1 second'"))
                )
            async with sessions() as session, session.begin():
                second = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="worker-b",
                    lease_for=timedelta(seconds=30),
                )
                assert second is not None and second.token != first.token
                assert second.fencing_generation == first.fencing_generation + 1

            async with sessions() as session, session.begin():
                with pytest.raises(LeaseLost):
                    await renew_job_lease(session, first, lease_for=timedelta(minutes=1))
                with pytest.raises(LeaseLost):
                    await lock_job_for_effects(session, first)
                with pytest.raises(LeaseLost):
                    await add_outbox_event(session, event, claim=first)
                with pytest.raises(LeaseLost):
                    await record_job_effect_once(
                        session, first, effect_key="usage", result={"charged": True}
                    )
                with pytest.raises(LeaseLost):
                    await mark_outbox_published(session, event.id, claim=first)
                with pytest.raises(LeaseLost):
                    await enqueue_job(
                        session,
                        lane=JobLane.APPLICATION,
                        kind="conversation:follow-up",
                        payload={},
                        deduplication_key=stale_followup_key,
                        owner_id=owner,
                        ordered_conversation=True,
                        claim=first,
                    )

            async with sessions() as session:
                persisted_event = await session.get(OutboxEvent, event.id)
                assert persisted_event is not None and persisted_event.published_at is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionReceipt)
                        .where(
                            ActionReceipt.owner_id == owner,
                            ActionReceipt.action_key == f"job:{first.job_id}:usage",
                        )
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(Job.deduplication_key == stale_followup_key)
                    )
                    == 0
                )
            async with sessions() as session, session.begin():
                await complete_job(session, second)
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_effect_receipt_prevents_replay_after_commit_before_job_completion(
    platform_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, key = uuid4(), f"committed-before-complete:{uuid4()}"
        kind = "conversation:crash-proof"
        effect_key = "create-plan"
        plan_id = str(uuid4())
        try:
            async with sessions() as session, session.begin():
                job = await enqueue_job(
                    session,
                    lane=JobLane.APPLICATION,
                    kind=kind,
                    payload={},
                    deduplication_key=key,
                    owner_id=owner,
                    ordered_conversation=True,
                    priority=10_000,
                )

            child_script = """
import os
import sys
from datetime import timedelta
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence import run_local_async
from pin.platform.jobs import JobLane, claim_job, record_job_effect_once

async def main():
    engine = create_engine(os.environ["PIN_TEST_PLATFORM_DATABASE_URL"])
    sessions = create_session_factory(engine)
    async with sessions() as session, session.begin():
        claim = await claim_job(
            session, lane=JobLane.APPLICATION, worker_id="crash-child-" + sys.argv[1],
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None and claim.kind == os.environ["PIN_CRASH_TEST_KIND"]
        if sys.argv[1] == "effect":
            receipt = await record_job_effect_once(
                session, claim, effect_key="create-plan",
                result={"plan_id": os.environ["PIN_CRASH_PLAN_ID"], "saved": True},
            )
            assert receipt.first_application
    await engine.dispose()

run_local_async(main)
print("COMMITTED", flush=True)
os._exit(79 if sys.argv[1] == "effect" else 77)
"""
            child_env = os.environ.copy()
            child_env["PIN_TEST_PLATFORM_DATABASE_URL"] = platform_database
            child_env["PIN_CRASH_TEST_KIND"] = kind
            child_env["PIN_CRASH_PLAN_ID"] = plan_id

            # The first child exits after the claim transaction commits. The
            # second exits after the effect receipt transaction commits.
            for phase, expected_code in (("claim", 77), ("effect", 79)):
                child = subprocess.run(
                    [sys.executable, "-c", child_script, phase],
                    cwd=ROOT,
                    env=child_env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                assert child.returncode == expected_code, child.stderr
                assert "COMMITTED" in child.stdout
                async with sessions() as session, session.begin():
                    await session.execute(
                        update(Job)
                        .where(Job.id == job.id)
                        .values(lease_until=text("clock_timestamp() - interval '1 second'"))
                    )
                    await session.execute(
                        update(ConversationLease)
                        .where(ConversationLease.owner_id == owner)
                        .values(lease_until=text("clock_timestamp() - interval '1 second'"))
                    )

            async with sessions() as session, session.begin():
                retry = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="recovered-worker",
                    lease_for=timedelta(seconds=30),
                )
                assert retry is not None and retry.job_id == job.id
                replay = await record_job_effect_once(
                    session,
                    retry,
                    effect_key=effect_key,
                    result={"plan_id": "must-not-replace", "saved": True},
                )
                assert not replay.first_application
                assert replay.result == {"plan_id": plan_id, "saved": True}
            async with sessions() as session, session.begin():
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionReceipt)
                        .where(
                            ActionReceipt.owner_id == owner,
                            ActionReceipt.action_key == f"job:{job.id}:{effect_key}",
                        )
                    )
                    == 1
                )
                await complete_job(session, retry)
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_per_owner_order_waits_for_speech_while_other_owners_and_delivery_progress(
    platform_database: str,
) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, other_owner = uuid4(), uuid4()
        try:
            async with sessions() as session, session.begin():
                await enqueue_job(
                    session,
                    lane=JobLane.APPLICATION,
                    kind="conversation:text-1",
                    payload={},
                    deduplication_key=f"order:{uuid4()}",
                    owner_id=owner,
                    ordered_conversation=True,
                    priority=100,
                )
                await enqueue_job(
                    session,
                    lane=JobLane.SPEECH,
                    kind="conversation:voice",
                    payload={},
                    deduplication_key=f"order:{uuid4()}",
                    owner_id=owner,
                    ordered_conversation=True,
                )
                await enqueue_job(
                    session,
                    lane=JobLane.APPLICATION,
                    kind="conversation:text-2",
                    payload={},
                    deduplication_key=f"order:{uuid4()}",
                    owner_id=owner,
                    ordered_conversation=True,
                )
                await enqueue_job(
                    session,
                    lane=JobLane.APPLICATION,
                    kind="conversation:other-owner",
                    payload={},
                    deduplication_key=f"order:{uuid4()}",
                    owner_id=other_owner,
                    ordered_conversation=True,
                )
                await enqueue_job(
                    session,
                    lane=JobLane.DELIVERY,
                    kind="notification:send",
                    payload={},
                    deduplication_key=f"order:{uuid4()}",
                    owner_id=owner,
                )

            async with sessions() as session, session.begin():
                first = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="app-1",
                    lease_for=timedelta(minutes=1),
                )
                assert first is not None
                assert first.owner_id == owner and first.conversation_sequence == 1
            async with sessions() as session, session.begin():
                assert (
                    await claim_job(
                        session,
                        lane=JobLane.SPEECH,
                        worker_id="speech-1",
                        lease_for=timedelta(minutes=1),
                    )
                    is None
                )
                other = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="app-2",
                    lease_for=timedelta(minutes=1),
                )
                delivery = await claim_job(
                    session,
                    lane=JobLane.DELIVERY,
                    worker_id="delivery-1",
                    lease_for=timedelta(minutes=1),
                )
                assert other is not None and other.owner_id == other_owner
                assert delivery is not None and delivery.lane is JobLane.DELIVERY
            async with sessions() as session, session.begin():
                await complete_job(session, first)
            async with sessions() as session, session.begin():
                speech = await claim_job(
                    session,
                    lane=JobLane.SPEECH,
                    worker_id="speech-2",
                    lease_for=timedelta(minutes=1),
                )
                assert speech is not None and speech.conversation_sequence == 2
            async with sessions() as session, session.begin():
                assert (
                    await claim_job(
                        session,
                        lane=JobLane.APPLICATION,
                        worker_id="app-3",
                        lease_for=timedelta(minutes=1),
                    )
                    is None
                )
            async with sessions() as session, session.begin():
                await complete_job(session, speech)
            async with sessions() as session, session.begin():
                final = await claim_job(
                    session,
                    lane=JobLane.APPLICATION,
                    worker_id="app-4",
                    lease_for=timedelta(minutes=1),
                )
                assert final is not None
                assert final.owner_id == owner and final.conversation_sequence == 3
            async with sessions() as session, session.begin():
                await complete_job(session, other)
                await complete_job(session, delivery)
                await complete_job(session, final)
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_poison_job_becomes_terminal_after_bounded_retries(platform_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session, session.begin():
                job = await enqueue_job(
                    session,
                    lane=JobLane.DELIVERY,
                    kind="notification:poison",
                    payload={},
                    deduplication_key=f"poison:{uuid4()}",
                    priority=10_000,
                    max_attempts=2,
                )
                job_id = job.id
            for expected in ("pending", "dead"):
                async with sessions() as session, session.begin():
                    claim = await claim_job(
                        session,
                        lane=JobLane.DELIVERY,
                        worker_id=f"poison-{expected}",
                        lease_for=timedelta(seconds=30),
                    )
                    assert claim is not None and claim.job_id == job_id
                    assert (
                        await fail_job(
                            session, claim, error="malformed payload", retry_after=timedelta(0)
                        )
                        == expected
                    )
            async with sessions() as session, session.begin():
                assert (
                    await claim_job(
                        session,
                        lane=JobLane.DELIVERY,
                        worker_id="poison-final",
                        lease_for=timedelta(seconds=30),
                    )
                    is None
                )
            async with sessions() as session:
                dead_job = await session.get(Job, job_id)
                assert dead_job is not None and dead_job.status == "dead"
                assert dead_job.attempts == 2 and dead_job.last_error == "malformed payload"
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_out_of_order_events_defer_then_drain_with_atomic_receipts(platform_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, aggregate, consumer = uuid4(), uuid4(), f"consumer-{uuid4()}"

        def event(version: int) -> EventEnvelope:
            return EventEnvelope(
                id=uuid4(),
                event_type="planning.changed",
                schema_version=1,
                aggregate_id=aggregate,
                aggregate_version=version,
                owner_id=owner,
                occurred_at=datetime.now(UTC),
                payload={"version": version},
            )

        first, second, third = event(1), event(2), event(3)

        async def apply(session, envelope: EventEnvelope) -> None:
            await session.merge(
                ActionReceipt(
                    owner_id=owner,
                    action_key=f"event:{aggregate}:{envelope.aggregate_version}",
                    result={"version": envelope.aggregate_version},
                )
            )

        try:
            async with sessions() as session, session.begin():
                await add_outbox_event(session, first)
                await add_outbox_event(session, second)
                await add_outbox_event(session, third)
            async with sessions() as session, session.begin():
                two = await consume_event(
                    session, consumer_name=consumer, event=second, handler=apply
                )
                three = await consume_event(
                    session, consumer_name=consumer, event=third, handler=apply
                )
                assert two.outcome is ConsumeOutcome.DEFERRED
                assert three.outcome is ConsumeOutcome.DEFERRED
            async with sessions() as session, session.begin():
                one = await consume_event(
                    session, consumer_name=consumer, event=first, handler=apply
                )
                duplicate = await consume_event(
                    session, consumer_name=consumer, event=first, handler=apply
                )
                assert one.outcome is ConsumeOutcome.APPLIED and one.last_version == 3
                assert duplicate.outcome is ConsumeOutcome.DUPLICATE
                stale_event = event(1)
                stale = await consume_event(
                    session, consumer_name=consumer, event=stale_event, handler=apply
                )
                assert stale.outcome is ConsumeOutcome.STALE
            async with sessions() as session:
                rows = list(
                    await session.scalars(
                        select(OutboxConsumption).where(
                            OutboxConsumption.consumer_name == consumer,
                            OutboxConsumption.aggregate_id == aggregate,
                        )
                    )
                )
                assert len(rows) == 4
                assert sum(row.status == "applied" for row in rows) == 3
                assert sum(row.status == "stale" for row in rows) == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionReceipt)
                        .where(
                            ActionReceipt.owner_id == owner,
                            ActionReceipt.action_key.like(f"event:{aggregate}:%"),
                        )
                    )
                    == 3
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)


def test_failed_consumer_transaction_rolls_back_receipt_and_effect(platform_database: str) -> None:
    async def scenario() -> None:
        engine = create_engine(platform_database)
        sessions = create_session_factory(engine)
        owner, aggregate, consumer = uuid4(), uuid4(), f"rollback-{uuid4()}"
        envelope = EventEnvelope(
            id=uuid4(),
            event_type="billing.reservation",
            schema_version=1,
            aggregate_id=aggregate,
            aggregate_version=1,
            owner_id=owner,
            occurred_at=datetime.now(UTC),
            payload={"reservation": "rollback"},
        )

        async def write_then_crash(session, event: EventEnvelope) -> None:
            session.add(
                ActionReceipt(
                    owner_id=owner,
                    action_key=f"rollback:{event.id}",
                    result={"applied": True},
                )
            )
            await session.flush()
            raise RuntimeError("simulated crash before consumer commit")

        async def apply(session, event: EventEnvelope) -> None:
            session.add(
                ActionReceipt(
                    owner_id=owner,
                    action_key=f"rollback:{event.id}",
                    result={"applied": True},
                )
            )

        try:
            async with sessions() as session, session.begin():
                await add_outbox_event(session, envelope)
            async with sessions() as session:
                with pytest.raises(RuntimeError, match="simulated crash"):
                    async with session.begin():
                        await consume_event(
                            session,
                            consumer_name=consumer,
                            event=envelope,
                            handler=write_then_crash,
                        )
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxConsumption)
                        .where(
                            OutboxConsumption.consumer_name == consumer,
                            OutboxConsumption.event_id == envelope.id,
                        )
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionReceipt)
                        .where(ActionReceipt.action_key == f"rollback:{envelope.id}")
                    )
                    == 0
                )
            async with sessions() as session, session.begin():
                applied = await consume_event(
                    session, consumer_name=consumer, event=envelope, handler=apply
                )
                assert applied.outcome is ConsumeOutcome.APPLIED
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxConsumption)
                        .where(
                            OutboxConsumption.consumer_name == consumer,
                            OutboxConsumption.event_id == envelope.id,
                            OutboxConsumption.status == "applied",
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionReceipt)
                        .where(ActionReceipt.action_key == f"rollback:{envelope.id}")
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    run_local_async(scenario)
