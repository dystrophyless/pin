"""PostgreSQL command receipt lookup and idempotent persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.persistence.models import ActionReceipt


async def lock_action(session: AsyncSession, owner_id: UUID, action_key: str) -> None:
    """Serialize one owner/action command until the surrounding transaction ends."""
    if not 1 <= len(action_key.strip()) <= 128:
        raise ValueError("action_key must contain 1 to 128 non-whitespace characters")
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"{owner_id}:{action_key}", 0)))
    )


async def find_action_receipt(
    session: AsyncSession, owner_id: UUID, action_key: str
) -> ActionReceipt | None:
    receipt = await session.scalar(
        select(ActionReceipt).where(
            ActionReceipt.owner_id == owner_id,
            ActionReceipt.action_key == action_key,
        )
    )
    return receipt


async def record_action_receipt(
    session: AsyncSession, owner_id: UUID, action_key: str, result: Any
) -> ActionReceipt:
    """Insert a receipt or return the original committed receipt on replay."""
    statement = (
        insert(ActionReceipt)
        .values(owner_id=owner_id, action_key=action_key, result=result)
        .on_conflict_do_nothing(index_elements=[ActionReceipt.owner_id, ActionReceipt.action_key])
        .returning(ActionReceipt)
    )
    receipt = (await session.execute(statement)).scalar_one_or_none()
    if receipt is not None:
        return receipt

    existing = await find_action_receipt(session, owner_id, action_key)
    if existing is None:
        raise RuntimeError("action receipt conflict has no matching owner/action receipt")
    return existing
