from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.application.accountability_reads import (
    AccountabilityOccurrence,
    AccountabilityOccurrencePage,
)
from pin.contexts.planning.infrastructure.accountability_read_store import (
    SqlAlchemyAccountabilityReadStore,
)


async def get_accountability_occurrence(
    session: AsyncSession,
    *,
    owner_id: UUID,
    occurrence_id: UUID,
    for_update: bool = False,
) -> AccountabilityOccurrence | None:
    return await SqlAlchemyAccountabilityReadStore(session).get_accountability_occurrence(
        owner_id=owner_id, occurrence_id=occurrence_id, for_update=for_update
    )


async def list_today_open_untimed(
    session: AsyncSession,
    *,
    owner_id: UUID,
    profile_zone: str,
    at: datetime,
    after_occurrence_id: UUID | None = None,
    limit: int = 100,
    for_update: bool = False,
) -> AccountabilityOccurrencePage:
    return await SqlAlchemyAccountabilityReadStore(session).list_today_open_untimed(
        owner_id=owner_id,
        profile_zone=profile_zone,
        at=at,
        after_occurrence_id=after_occurrence_id,
        limit=limit,
        for_update=for_update,
    )
