"""Notifications-owned account deletion adapter."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.notifications.infrastructure import models as _notification_models  # noqa: F401
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="notifications", owner_id=owner_id)
