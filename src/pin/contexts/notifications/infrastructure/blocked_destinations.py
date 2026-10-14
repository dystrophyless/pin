"""Shared persisted destination-block policy across delivery attempt streams."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.notifications.domain.delivery import DeliveryOutcomeKind
from pin.contexts.notifications.infrastructure.models import (
    AccountabilityDeliveryAttemptRecord,
    DeliveryAttemptRecord,
)


async def destination_is_blocked(session: AsyncSession, owner_id: UUID, target_id: int) -> bool:
    """Check known provider blocks from generic and Accountability attempts."""
    generic_block = await session.scalar(
        select(DeliveryAttemptRecord.id)
        .where(
            DeliveryAttemptRecord.owner_id == owner_id,
            DeliveryAttemptRecord.target_id == target_id,
            DeliveryAttemptRecord.outcome == DeliveryOutcomeKind.BLOCKED.value,
        )
        .limit(1)
    )
    if generic_block is not None:
        return True
    accountability_block = await session.scalar(
        select(AccountabilityDeliveryAttemptRecord.id)
        .where(
            AccountabilityDeliveryAttemptRecord.owner_id == owner_id,
            AccountabilityDeliveryAttemptRecord.target_id == target_id,
            AccountabilityDeliveryAttemptRecord.outcome == DeliveryOutcomeKind.BLOCKED.value,
        )
        .limit(1)
    )
    return accountability_block is not None


__all__ = ["destination_is_blocked"]
