"""Speech-owned transcript deletion and raw-audio cleanup."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.speech.infrastructure.models import SpeechRequestRecord
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="speech", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(SpeechRequestRecord)
        .where(SpeechRequestRecord.created_at < cutoff)
        .returning(SpeechRequestRecord.job_id)
    )
    return len(result.scalars().all())
