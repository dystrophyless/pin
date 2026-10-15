"""Planning-owned account deletion and text-retention adapters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.infrastructure import agenda as _agenda_models  # noqa: F401
from pin.contexts.planning.infrastructure import home_zone_change_models as _zone_models  # noqa: F401
from pin.contexts.planning.infrastructure import models as _planning_models  # noqa: F401
from pin.contexts.planning.infrastructure import mutation_models as _mutation_models  # noqa: F401
from pin.contexts.planning.infrastructure import series_models as _series_models  # noqa: F401
from pin.contexts.planning.infrastructure.home_zone_change_models import (
    HomeZoneChangePreviewRecord,
)
from pin.contexts.planning.infrastructure.mutation_models import MutationPreviewRecord
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="planning", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    removed = 0
    for model in (MutationPreviewRecord, HomeZoneChangePreviewRecord):
        result = await session.execute(
            delete(model).where(model.created_at < cutoff).returning(*model.__table__.primary_key)
        )
        removed += len(result.all())
    return removed
