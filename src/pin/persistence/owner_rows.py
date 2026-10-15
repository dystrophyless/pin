"""Mechanical helpers for context-owned deletion adapters."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.persistence.models import Base


async def delete_owner_rows(session: AsyncSession, *, schema: str, owner_id: UUID) -> int:
    """Delete rows owned by one context, child tables before their parents."""
    removed = 0
    for table in reversed(Base.metadata.sorted_tables):
        if table.schema != schema or "owner_id" not in table.c:
            continue
        result = await session.execute(
            delete(table).where(table.c.owner_id == owner_id).returning(*table.primary_key.columns)
        )
        removed += len(result.all())
    return removed
