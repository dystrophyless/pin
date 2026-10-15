"""Conversation-owned account deletion and 30-day text retention adapters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.conversation.infrastructure import models as _conversation_models  # noqa: F401
from pin.contexts.conversation.infrastructure.capture_models import (
    CaptureConfirmationRecord,
    CaptureDraftRecord,
    CaptureInputRecord,
)
from pin.contexts.conversation.infrastructure.models import ConversationTurnResultRecord
from pin.persistence.owner_rows import delete_owner_rows


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    return await delete_owner_rows(session, schema="conversation", owner_id=owner_id)


async def purge_expired_text(session: AsyncSession, cutoff: datetime) -> int:
    removed = 0
    for model, timestamp in (
        (CaptureConfirmationRecord, CaptureConfirmationRecord.expires_at),
        (CaptureInputRecord, CaptureInputRecord.created_at),
        (CaptureDraftRecord, CaptureDraftRecord.updated_at),
        (ConversationTurnResultRecord, ConversationTurnResultRecord.created_at),
    ):
        result = await session.execute(
            delete(model).where(timestamp < cutoff).returning(*model.__table__.primary_key)
        )
        removed += len(result.all())
    return removed
