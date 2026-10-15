"""Idempotent consumer for Planning changes that open or close accountability cycles."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.application.contracts import (
    CurrentOccurrenceReader,
)
from pin.contexts.accountability.domain.cycles import CycleState
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityOutcomeReceiptRecord,
    CheckInCycleRecord,
)
from pin.contexts.accountability.infrastructure.scheduler import synchronize_occurrence
from pin.contexts.billing.api import AccountLifecycle, SqlAlchemyAccessUsageStore
from pin.platform.models import OutboxEvent

PLANNING_CYCLE_EVENTS = frozenset(
    {
        "planning.plan_created",
        "planning.plan_rescheduled",
        "planning.series_occurrence_created",
        "planning.series_occurrence_rescheduled",
        "planning.occurrence_completed",
        "planning.occurrence_cancelled",
    }
)


async def consume_planning_cycle_events(
    session: AsyncSession,
    *,
    current_occurrences: CurrentOccurrenceReader,
    at: datetime,
    limit: int = 100,
) -> int:
    """Read current Planning truth for each committed event; replay is receipt-safe."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("accountability event clock must be timezone-aware")
    if not 1 <= limit <= 1000:
        raise ValueError("event limit must be between 1 and 1000")
    receipt_exists = exists(
        select(1).where(
            AccountabilityOutcomeReceiptRecord.owner_id == OutboxEvent.owner_id,
            AccountabilityOutcomeReceiptRecord.event_id == OutboxEvent.id,
        )
    )
    candidates = list(
        (
            await session.execute(
                select(OutboxEvent.id, OutboxEvent.owner_id)
                .where(OutboxEvent.event_type.in_(PLANNING_CYCLE_EVENTS), ~receipt_exists)
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(limit)
            )
        ).all()
    )
    consumed = 0
    lifecycle_by_owner = {}
    for owner_id in sorted({owner_id for _, owner_id in candidates}, key=lambda value: value.int):
        lifecycle_by_owner[owner_id] = (
            await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(owner_id)
        )
    for event_id, owner_id in candidates:
        lifecycle = lifecycle_by_owner[owner_id]
        if lifecycle is AccountLifecycle.DELETED:
            continue
        event = await session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            continue
        if (
            await session.scalar(
                select(AccountabilityOutcomeReceiptRecord.id).where(
                    AccountabilityOutcomeReceiptRecord.owner_id == owner_id,
                    AccountabilityOutcomeReceiptRecord.event_id == event_id,
                )
            )
            is not None
        ):
            continue
        current = await current_occurrences.get_current_occurrence(
            event.owner_id, event.aggregate_id
        )
        if current is not None:
            await synchronize_occurrence(session, current, now=at)
        else:
            cycles = list(
                (
                    await session.scalars(
                        select(CheckInCycleRecord)
                        .where(
                            CheckInCycleRecord.owner_id == event.owner_id,
                            CheckInCycleRecord.occurrence_id == event.aggregate_id,
                            CheckInCycleRecord.state == CycleState.OPEN.value,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for cycle in cycles:
                cycle.state = CycleState.CANCELLED.value
        session.add(
            AccountabilityOutcomeReceiptRecord(
                owner_id=event.owner_id,
                event_id=event.id,
            )
        )
        await session.flush()
        consumed += 1
    return consumed
