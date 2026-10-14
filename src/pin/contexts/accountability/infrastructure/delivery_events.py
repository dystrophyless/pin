"""Consume durable Notifications attempt outcomes with event-level deduplication."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.infrastructure.models import AccountabilityOutcomeReceiptRecord
from pin.contexts.accountability.infrastructure.scheduler import apply_delivery_outcome
from pin.platform.contracts import EventEnvelope
from pin.platform.models import OutboxEvent


async def consume_delivery_outcomes(
    session: AsyncSession,
    *,
    at: datetime,
    limit: int = 100,
) -> int:
    """Apply missed confirmed events from their persisted event time, never worker restart time."""
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
    events = list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "notifications.delivery_attempt_outcome",
                    ~receipt_exists,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for event in events:
        try:
            envelope = EventEnvelope.from_dict(event.payload)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Accountability delivery outcome has an invalid envelope") from error
        if (
            envelope.id != event.id
            or envelope.event_type != event.event_type
            or envelope.owner_id != event.owner_id
            or envelope.occurred_at != event.occurred_at
            or envelope.schema_version != 1
            or not isinstance(envelope.payload, dict)
        ):
            raise ValueError("Accountability delivery outcome envelope does not match its receipt")
        payload = envelope.payload
        raw_intent = payload.get("accountability_intent_id")
        if raw_intent is not None:
            raw_attempt = payload.get("attempt_number")
            outcome = payload.get("outcome")
            retrying = payload.get("retrying")
            if (
                not isinstance(raw_intent, str)
                or type(raw_attempt) is not int
                or raw_attempt < 1
                or not isinstance(outcome, str)
                or outcome not in {"confirmed", "failed", "unknown", "blocked"}
                or type(retrying) is not bool
            ):
                raise ValueError("Accountability delivery outcome has an invalid payload")
            try:
                intent_id = UUID(raw_intent)
            except ValueError as error:
                raise ValueError(
                    "Accountability delivery outcome has an invalid payload"
                ) from error
            await apply_delivery_outcome(
                session,
                event_id=event.id,
                owner_id=event.owner_id,
                intent_id=intent_id,
                outcome=outcome,
                occurred_at=event.occurred_at,
                attempt_number=raw_attempt,
                retrying=retrying,
            )
        else:
            session.add(
                AccountabilityOutcomeReceiptRecord(
                    owner_id=event.owner_id,
                    event_id=event.id,
                )
            )
        await session.flush()
    return len(events)
