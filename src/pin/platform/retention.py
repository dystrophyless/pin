"""Platform-owned deletion and 30-day purge adapters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.persistence.owner_rows import delete_owner_rows
from pin.platform import models as _platform_models  # noqa: F401
from pin.platform.models import (
    ConsumerCursor,
    InboxUpdate,
    Job,
    OutboxConsumption,
    OutboxEvent,
)


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    event_ids = select(OutboxEvent.id).where(OutboxEvent.owner_id == owner_id)
    aggregates = select(OutboxEvent.aggregate_id).where(OutboxEvent.owner_id == owner_id)
    await session.execute(
        delete(OutboxConsumption).where(OutboxConsumption.event_id.in_(event_ids))
    )
    await session.execute(delete(ConsumerCursor).where(ConsumerCursor.aggregate_id.in_(aggregates)))
    return await delete_owner_rows(session, schema="platform", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    event_ids = select(OutboxEvent.id).where(OutboxEvent.occurred_at < cutoff)
    aggregates = select(OutboxEvent.aggregate_id).where(OutboxEvent.occurred_at < cutoff)
    removed = 0
    for model, statement in (
        (
            OutboxConsumption,
            delete(OutboxConsumption).where(OutboxConsumption.event_id.in_(event_ids)),
        ),
        (ConsumerCursor, delete(ConsumerCursor).where(ConsumerCursor.aggregate_id.in_(aggregates))),
        (Job, delete(Job).where(Job.created_at < cutoff)),
        (InboxUpdate, delete(InboxUpdate).where(InboxUpdate.received_at < cutoff)),
        (OutboxEvent, delete(OutboxEvent).where(OutboxEvent.occurred_at < cutoff)),
    ):
        result = await session.execute(statement.returning(*model.__table__.primary_key))
        removed += len(result.all())
    return removed
