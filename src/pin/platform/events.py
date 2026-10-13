"""Transactional outbox persistence and version-ordered consumer receipts."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.platform.contracts import EventEnvelope
from pin.platform.jobs import JobClaim, lock_job_for_effects
from pin.platform.models import ConsumerCursor, OutboxConsumption, OutboxEvent


class ConsumeOutcome(StrEnum):
    APPLIED = "applied"
    DEFERRED = "deferred"
    STALE = "stale"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    outcome: ConsumeOutcome
    last_version: int


EventHandler = Callable[[AsyncSession, EventEnvelope], object | Awaitable[object]]


async def add_outbox_event(
    session: AsyncSession, event: EventEnvelope, *, claim: JobClaim | None = None
) -> None:
    """Append an event to the caller's business transaction."""
    if claim is not None:
        await lock_job_for_effects(session, claim)
    session.add(
        OutboxEvent(
            id=event.id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            owner_id=event.owner_id,
            occurred_at=event.occurred_at,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=event.to_dict(),
        )
    )


async def mark_outbox_published(
    session: AsyncSession, event_id: UUID, *, claim: JobClaim | None = None
) -> bool:
    """Record a successful transport handoff; external exactly-once is not claimed."""
    if claim is not None:
        await lock_job_for_effects(session, claim)
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
    )
    if event is None or event.published_at is not None:
        return False
    event.published_at = await session.scalar(select(func.clock_timestamp()))
    return True


async def _get_cursor(session: AsyncSession, consumer_name: str, aggregate_id: UUID) -> int:
    await session.execute(
        insert(ConsumerCursor)
        .values(consumer_name=consumer_name, aggregate_id=aggregate_id, last_version=0)
        .on_conflict_do_nothing()
    )
    return int(
        await session.scalar(
            select(ConsumerCursor.last_version)
            .where(
                ConsumerCursor.consumer_name == consumer_name,
                ConsumerCursor.aggregate_id == aggregate_id,
            )
            .with_for_update()
        )
    )


async def _call_handler(session: AsyncSession, handler: EventHandler, event: EventEnvelope) -> None:
    result = handler(session, event)
    if inspect.isawaitable(result):
        await result


async def consume_event(
    session: AsyncSession,
    *,
    consumer_name: str,
    event: EventEnvelope,
    handler: EventHandler,
    claim: JobClaim | None = None,
) -> ConsumeResult:
    """Apply the next aggregate version once, defer gaps, and receipt stale versions."""
    if not consumer_name.strip() or len(consumer_name) > 120:
        raise ValueError("consumer_name must contain 1 to 120 characters")
    if claim is not None:
        await lock_job_for_effects(session, claim)

    previous = await session.scalar(
        select(OutboxConsumption).where(
            OutboxConsumption.consumer_name == consumer_name,
            OutboxConsumption.event_id == event.id,
        )
    )
    if previous is not None:
        cursor = await session.scalar(
            select(ConsumerCursor.last_version).where(
                ConsumerCursor.consumer_name == consumer_name,
                ConsumerCursor.aggregate_id == event.aggregate_id,
            )
        )
        outcome = (
            ConsumeOutcome.DUPLICATE
            if previous.status == "applied"
            else ConsumeOutcome.STALE
            if previous.status == "stale"
            else ConsumeOutcome.DEFERRED
        )
        return ConsumeResult(outcome, int(cursor or 0))

    last_version = await _get_cursor(session, consumer_name, event.aggregate_id)
    if event.aggregate_version <= last_version:
        session.add(
            OutboxConsumption(
                consumer_name=consumer_name,
                event_id=event.id,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                status="stale",
                envelope=event.to_dict(),
            )
        )
        return ConsumeResult(ConsumeOutcome.STALE, last_version)

    receipt = OutboxConsumption(
        consumer_name=consumer_name,
        event_id=event.id,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        status="pending",
        envelope=event.to_dict(),
    )
    session.add(receipt)
    await session.flush()
    if event.aggregate_version != last_version + 1:
        return ConsumeResult(ConsumeOutcome.DEFERRED, last_version)

    await _call_handler(session, handler, event)
    receipt.status = "applied"
    receipt.applied_at = await session.scalar(select(func.clock_timestamp()))
    last_version += 1
    await session.execute(
        update(ConsumerCursor)
        .where(
            ConsumerCursor.consumer_name == consumer_name,
            ConsumerCursor.aggregate_id == event.aggregate_id,
        )
        .values(last_version=last_version)
    )
    last_version = await _drain_pending(
        session, consumer_name, event.aggregate_id, last_version, handler
    )
    return ConsumeResult(ConsumeOutcome.APPLIED, last_version)


async def _drain_pending(
    session: AsyncSession,
    consumer_name: str,
    aggregate_id: UUID,
    last_version: int,
    handler: EventHandler,
) -> int:
    """Apply contiguous events saved by an earlier out-of-order delivery."""
    while True:
        pending = await session.scalar(
            select(OutboxConsumption)
            .where(
                OutboxConsumption.consumer_name == consumer_name,
                OutboxConsumption.aggregate_id == aggregate_id,
                OutboxConsumption.aggregate_version == last_version + 1,
                OutboxConsumption.status == "pending",
            )
            .with_for_update()
        )
        if pending is None:
            return last_version
        await _call_handler(session, handler, EventEnvelope.from_dict(pending.envelope))
        pending.status = "applied"
        pending.applied_at = await session.scalar(select(func.clock_timestamp()))
        last_version += 1
        await session.execute(
            update(ConsumerCursor)
            .where(
                ConsumerCursor.consumer_name == consumer_name,
                ConsumerCursor.aggregate_id == aggregate_id,
            )
            .values(last_version=last_version)
        )


async def reconcile_pending_events(
    session: AsyncSession,
    *,
    consumer_name: str,
    aggregate_id: UUID,
    handler: EventHandler,
) -> int:
    """Reconciliation hook for deferred receipts after a missing event is recovered."""
    last_version = await _get_cursor(session, consumer_name, aggregate_id)
    return await _drain_pending(session, consumer_name, aggregate_id, last_version, handler)


async def pending_outbox_events(session: AsyncSession, *, limit: int = 100) -> list[EventEnvelope]:
    """Return unpublished envelopes for a bounded publisher poll."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(limit)
    )
    return [EventEnvelope.from_dict(row.payload) for row in rows]


async def pending_event_receipts(
    session: AsyncSession, *, consumer_name: str, limit: int = 100
) -> list[EventEnvelope]:
    """Expose deferred event gaps for consumer-side reconciliation/operations."""
    rows = await session.scalars(
        select(OutboxConsumption)
        .where(
            OutboxConsumption.consumer_name == consumer_name,
            OutboxConsumption.status == "pending",
        )
        .order_by(OutboxConsumption.aggregate_id, OutboxConsumption.aggregate_version)
        .limit(limit)
    )
    return [EventEnvelope.from_dict(row.envelope) for row in rows]
