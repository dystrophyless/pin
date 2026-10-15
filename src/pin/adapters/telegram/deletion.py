"""Deletion composition root for context-owned account erasure adapters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.adapters.telegram.mutation_runtime_store import MutationRuntimePendingRecord
from pin.adapters.telegram.presentation.tokens import TelegramCallbackTokenRecord
from pin.contexts.accountability.infrastructure.deletion import purge_owner as purge_accountability
from pin.contexts.billing.api import (
    AccountLifecycle,
    MarkAccountDeleted,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.billing.infrastructure.deletion import purge_owner_operational_data
from pin.contexts.conversation.infrastructure.deletion import purge_owner as purge_conversation
from pin.contexts.identity.infrastructure.deletion import purge_owner as purge_identity
from pin.contexts.notifications.infrastructure.deletion import purge_owner as purge_notifications
from pin.contexts.planning.infrastructure.deletion import purge_owner as purge_planning
from pin.contexts.speech.infrastructure.deletion import purge_owner as purge_speech
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles
from pin.persistence.retention import purge_owner as purge_persistence
from pin.platform.retention import purge_owner as purge_platform


async def purge_owner(session: AsyncSession, owner_id: UUID) -> int:
    removed = 0
    for model in (
        MutationRuntimePendingRecord,
        TelegramCallbackTokenRecord,
        TelegramOwnerRecord,
    ):
        result = await session.execute(
            delete(model).where(model.owner_id == owner_id).returning(*model.__table__.primary_key)
        )
        removed += len(result.all())
    return removed


async def purge_account(session: AsyncSession, owner_id: UUID, at: datetime) -> None:
    """Run all context adapters in the caller's transaction after ledger export."""
    billing = SqlAlchemyAccessUsageStore(session)
    lifecycle = await billing.lock_account_lifecycle(owner_id)
    exists = lifecycle is not AccountLifecycle.MISSING
    if exists and not await billing.mark_account_deleted(MarkAccountDeleted(owner_id, at)):
        raise RuntimeError("Billing account disappeared during deletion")
    await purge_notifications(session, owner_id)
    await purge_accountability(session, owner_id)
    await purge_speech(session, owner_id)
    await purge_conversation(session, owner_id)
    await purge_planning(session, owner_id)
    await purge_identity(session, owner_id)
    await purge_platform(session, owner_id)
    await purge_persistence(session, owner_id)
    await purge_owner(session, owner_id)
    if exists:
        await purge_owner_operational_data(session, owner_id=owner_id, at=at)
        await billing.finish_account_deletion(owner_id)
    # The generated owner prefix prevents touching another account's active upload.
    PrivateAudioFiles().remove_owner(owner_id)
