"""Accountability-owned account deletion and text-retention adapters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.infrastructure import models as _accountability_models  # noqa: F401
from pin.contexts.accountability.infrastructure.models import CheckInCycleRecord
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="accountability", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(CheckInCycleRecord)
        .where(CheckInCycleRecord.created_at < cutoff)
        .returning(CheckInCycleRecord.id)
    )
    return len(result.scalars().all())
