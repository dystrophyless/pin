"""Account deletion orchestration across context-owned purge adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.accountability.infrastructure.deletion import (
    purge_expired_text as purge_accountability_text,
)
from pin.contexts.billing.api import AccountLifecycle, SqlAlchemyAccessUsageStore
from pin.contexts.conversation.infrastructure.deletion import (
    purge_expired_text as purge_conversation_text,
)
from pin.contexts.planning.infrastructure.deletion import purge_expired_text as purge_planning_text
from pin.contexts.retention.application.contracts import ExpiredTextPurger, OwnerPurger
from pin.contexts.retention.domain.deletion import DeletionState
from pin.contexts.retention.infrastructure.ledger import ensure_ledger, read_tombstones
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)
from pin.contexts.retention.infrastructure.store import (
    cancel_deletion_preview,
    confirm_deletion_preview,
    create_deletion_preview,
    export_tombstones,
    record_replayed_tombstone,
)
from pin.contexts.speech.infrastructure.deletion import purge_expired_text as purge_speech_text
from pin.persistence.receipts import lock_action
from pin.persistence.retention import purge_expired_text as purge_persistence_text
from pin.platform.retention import purge_expired_text as purge_platform_text

TEXT_RETENTION_PURGERS = (
    purge_accountability_text,
    purge_conversation_text,
    purge_persistence_text,
    purge_planning_text,
    purge_platform_text,
    purge_speech_text,
)


async def finish_pending_deletions(
    sessions: async_sessionmaker[AsyncSession],
    *,
    artifact: Path,
    now: datetime,
    purge_owner: OwnerPurger[AsyncSession],
) -> int:
    """Export confirmed tombstones before atomically purging operational schemas."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("deletion clock must be timezone-aware")
    at = now.astimezone(UTC)
    ensure_ledger(artifact)
    async with sessions() as session, session.begin():
        await export_tombstones(session, path=artifact, now=at)
    async with sessions() as session:
        candidates = tuple(
            await session.execute(
                select(DeletionRequestRecord.id, DeletionRequestRecord.owner_id)
                .where(DeletionRequestRecord.state == DeletionState.PENDING_EXPORT.value)
                .order_by(DeletionRequestRecord.confirmed_at, DeletionRequestRecord.id)
            )
        )
    completed = 0
    for request_id, owner_id in candidates:
        async with sessions() as session, session.begin():
            await lock_action(session, owner_id, "identity.processing_consent")
            await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(owner_id)
            request = await session.scalar(
                select(DeletionRequestRecord)
                .where(
                    DeletionRequestRecord.id == request_id,
                    DeletionRequestRecord.owner_id == owner_id,
                    DeletionRequestRecord.state == DeletionState.PENDING_EXPORT.value,
                )
                .with_for_update()
            )
            if request is None:
                continue
            tombstone = await session.scalar(
                select(DeletionTombstoneRecord).where(
                    DeletionTombstoneRecord.owner_id == owner_id,
                    DeletionTombstoneRecord.deleted_at == request.confirmed_at,
                    DeletionTombstoneRecord.exported_at.is_not(None),
                )
            )
            if tombstone is None:
                continue
            await purge_owner(session, owner_id, at)
            request.state = DeletionState.COMPLETED.value
            request.completed_at = at
            completed += 1
    return completed


async def replay_deletion_artifact(
    sessions: async_sessionmaker[AsyncSession],
    *,
    artifact: Path,
    now: datetime,
    purge_owner: OwnerPurger[AsyncSession],
) -> int:
    """Apply deletions recorded after a restored snapshot before workers are started."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("replay clock must be timezone-aware")
    at = now.astimezone(UTC)
    tombstones = read_tombstones(artifact)
    completed = 0
    for item in tombstones:
        async with sessions() as session, session.begin():
            await lock_action(session, item.owner_id, "identity.processing_consent")
            lifecycle = await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(
                item.owner_id
            )
            previously_recorded = await record_replayed_tombstone(session, item, replayed_at=at)
            # A retained tombstone plus an active account is a later, legitimate
            # TR-03 return generation; never erase its newly created plans.
            later_generation = previously_recorded and lifecycle is AccountLifecycle.PRESENT
            if not later_generation:
                await purge_owner(session, item.owner_id, at)
                await session.execute(
                    update(DeletionRequestRecord)
                    .where(
                        DeletionRequestRecord.owner_id == item.owner_id,
                        DeletionRequestRecord.state == DeletionState.PENDING_EXPORT.value,
                    )
                    .values(state=DeletionState.COMPLETED.value, completed_at=at)
                )
            completed += 1
    return completed


async def purge_expired_text_once(
    sessions: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    purgers: tuple[ExpiredTextPurger[AsyncSession], ...],
) -> int:
    """Erase text-bearing operational locations older than the adopted 30-day window."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("retention clock must be timezone-aware")
    cutoff = now.astimezone(UTC) - timedelta(days=30)
    async with sessions() as session, session.begin():
        removed = 0
        for purge in purgers:
            removed += await purge(session, cutoff)
        return removed


__all__ = [
    "TEXT_RETENTION_PURGERS",
    "cancel_deletion_preview",
    "confirm_deletion_preview",
    "create_deletion_preview",
    "finish_pending_deletions",
    "purge_expired_text_once",
    "replay_deletion_artifact",
]
