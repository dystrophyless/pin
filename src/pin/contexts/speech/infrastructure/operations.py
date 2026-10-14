"""Speech-owned, transcript-free voice outcome projection."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.speech.api import SpeechOperations
from pin.contexts.speech.infrastructure.models import SpeechRequestRecord


async def read_speech_operations(session: AsyncSession) -> SpeechOperations:
    grouped = await session.execute(
        select(SpeechRequestRecord.state, func.count()).group_by(SpeechRequestRecord.state)
    )
    failed_ids = await session.scalars(
        select(SpeechRequestRecord.job_id)
        .where(SpeechRequestRecord.state == "failed")
        .order_by(SpeechRequestRecord.updated_at.desc())
        .limit(25)
    )
    return SpeechOperations(
        states={state: int(count) for state, count in grouped},
        failed_job_ids=tuple(str(value) for value in failed_ids),
    )
