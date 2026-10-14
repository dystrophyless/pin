from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.application.review_roster import PreferencesPage
from pin.contexts.identity.infrastructure.models import PreferencesRecord
from pin.contexts.identity.infrastructure.store import _snapshot


async def list_review_profiles(
    session: AsyncSession,
    *,
    after_owner_id: UUID | None = None,
    limit: int = 100,
) -> PreferencesPage:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("review profile limit must be between 1 and 1000")
    statement = select(PreferencesRecord).order_by(PreferencesRecord.owner_id).limit(limit + 1)
    if after_owner_id is not None:
        statement = statement.where(PreferencesRecord.owner_id > after_owner_id)
    records = (await session.scalars(statement)).all()
    has_more = len(records) > limit
    page_records = records[:limit]
    return PreferencesPage(
        items=tuple(_snapshot(record.owner_id, record) for record in page_records),
        next_owner_id=page_records[-1].owner_id if has_more else None,
    )
