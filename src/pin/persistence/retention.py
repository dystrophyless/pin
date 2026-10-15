"""Persistence-owned age-based removal of completed idempotency receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.persistence.models import ActionReceipt
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="pin_persistence", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(ActionReceipt).where(ActionReceipt.created_at < cutoff).returning(ActionReceipt.id)
    )
    return len(result.scalars().all())
