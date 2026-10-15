"""Caller-UoW persistence for confirmed account deletion and artifact replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.api import (
    AccountLifecycle,
    MarkAccountDeleted,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.retention.domain.deletion import (
    DELETION_PREVIEW_TTL_SECONDS,
    DeletionPreview,
    DeletionRejected,
    DeletionState,
    DeletionTombstone,
)
from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)


def _instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retention timestamp must be timezone-aware")
    return value.astimezone(UTC)


async def create_deletion_preview(
    session: AsyncSession, *, owner_id: UUID, now: datetime
) -> DeletionPreview:
    at = _instant(now)
    billing = SqlAlchemyAccessUsageStore(session)
    if await billing.lock_account_lifecycle(owner_id) is not AccountLifecycle.PRESENT:
        raise DeletionRejected("account is unavailable")
    prior = await session.scalars(
        select(DeletionRequestRecord)
        .where(
            DeletionRequestRecord.owner_id == owner_id,
            DeletionRequestRecord.state == DeletionState.PREVIEWED.value,
        )
        .with_for_update()
    )
    for record in prior:
        record.state = DeletionState.CANCELLED.value
        record.version += 1
    row = DeletionRequestRecord(
        id=uuid4(),
        owner_id=owner_id,
        version=1,
        state=DeletionState.PREVIEWED.value,
        created_at=at,
        expires_at=at + timedelta(seconds=DELETION_PREVIEW_TTL_SECONDS),
    )
    session.add(row)
    await session.flush()
    return DeletionPreview(row.id, owner_id, row.version, row.expires_at)


async def get_current_deletion_preview(
    session: AsyncSession, *, owner_id: UUID, now: datetime
) -> DeletionPreview | None:
    """Read the current unexpired preview for one owner for callback version fencing."""
    at = _instant(now)
    row = await session.scalar(
        select(DeletionRequestRecord)
        .where(
            DeletionRequestRecord.owner_id == owner_id,
            DeletionRequestRecord.state == DeletionState.PREVIEWED.value,
            DeletionRequestRecord.expires_at > at,
        )
        .order_by(DeletionRequestRecord.created_at.desc(), DeletionRequestRecord.id)
        .limit(1)
    )
    if row is None:
        return None
    return DeletionPreview(row.id, row.owner_id, row.version, row.expires_at)


async def cancel_deletion_preview(
    session: AsyncSession,
    *,
    owner_id: UUID,
    preview_id: UUID,
    expected_version: int,
    now: datetime,
) -> bool:
    at = _instant(now)
    row = await session.scalar(
        select(DeletionRequestRecord)
        .where(
            DeletionRequestRecord.id == preview_id,
            DeletionRequestRecord.owner_id == owner_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DeletionRejected("deletion preview does not belong to this owner")
    if row.state == DeletionState.CANCELLED.value:
        return False
    if (
        row.state != DeletionState.PREVIEWED.value
        or row.version != expected_version
        or row.expires_at <= at
    ):
        raise DeletionRejected("deletion preview is stale or expired")
    row.state = DeletionState.CANCELLED.value
    row.version += 1
    await session.flush()
    return True


async def confirm_deletion_preview(
    session: AsyncSession,
    *,
    owner_id: UUID,
    preview_id: UUID,
    expected_version: int,
    now: datetime,
) -> DeletionTombstone:
    at = _instant(now)
    billing = SqlAlchemyAccessUsageStore(session)
    lifecycle = await billing.lock_account_lifecycle(owner_id)
    row = await session.scalar(
        select(DeletionRequestRecord)
        .where(
            DeletionRequestRecord.id == preview_id,
            DeletionRequestRecord.owner_id == owner_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DeletionRejected("deletion preview does not belong to this owner")
    if row.state in {DeletionState.PENDING_EXPORT.value, DeletionState.COMPLETED.value}:
        existing_tombstone = await session.scalar(
            select(DeletionTombstoneRecord).where(
                DeletionTombstoneRecord.owner_id == owner_id,
                DeletionTombstoneRecord.deleted_at == row.confirmed_at,
            )
        )
        if existing_tombstone is None:
            raise RuntimeError("confirmed deletion is missing its replay tombstone")
        return DeletionTombstone(
            existing_tombstone.event_id, owner_id, existing_tombstone.deleted_at
        )
    if (
        row.state != DeletionState.PREVIEWED.value
        or row.version != expected_version
        or row.expires_at <= at
    ):
        raise DeletionRejected("deletion preview is stale or expired")
    if lifecycle is not AccountLifecycle.PRESENT:
        raise DeletionRejected("account is unavailable")
    if not await billing.mark_account_deleted(MarkAccountDeleted(owner_id, at)):
        raise DeletionRejected("account is unavailable")
    deletion_tombstone = DeletionTombstone(uuid4(), owner_id, at)
    session.add(
        DeletionTombstoneRecord(
            event_id=deletion_tombstone.event_id,
            owner_id=owner_id,
            deleted_at=at,
            artifact_version=1,
        )
    )
    row.state = DeletionState.PENDING_EXPORT.value
    row.confirmed_at = at
    row.version += 1
    await session.flush()
    return deletion_tombstone


async def export_tombstones(session: AsyncSession, *, path: Path, now: datetime) -> int:
    """Export before deletion, then mark receipts; duplicate append after a crash is safe."""
    from pin.contexts.retention.infrastructure.ledger import append_tombstones

    at = _instant(now)
    records = tuple(
        await session.scalars(
            select(DeletionTombstoneRecord)
            .where(DeletionTombstoneRecord.exported_at.is_(None))
            .order_by(DeletionTombstoneRecord.deleted_at, DeletionTombstoneRecord.event_id)
            .with_for_update(skip_locked=True)
        )
    )
    if not records:
        return 0
    append_tombstones(
        path,
        tuple(DeletionTombstone(row.event_id, row.owner_id, row.deleted_at) for row in records),
    )
    for row in records:
        row.exported_at = at
    await session.flush()
    return len(records)


async def record_replayed_tombstone(
    session: AsyncSession, item: DeletionTombstone, *, replayed_at: datetime
) -> bool:
    at = _instant(replayed_at)
    existing = await session.scalar(
        select(DeletionTombstoneRecord)
        .where(DeletionTombstoneRecord.event_id == item.event_id)
        .with_for_update()
    )
    if existing is not None:
        if existing.owner_id != item.owner_id or existing.deleted_at != item.deleted_at:
            raise ValueError("replayed deletion event conflicts with persisted history")
        existing.exported_at = existing.exported_at or at
        return True
    inserted = await session.scalar(
        pg_insert(DeletionTombstoneRecord)
        .values(
            event_id=item.event_id,
            owner_id=item.owner_id,
            deleted_at=item.deleted_at,
            artifact_version=1,
            exported_at=at,
        )
        .on_conflict_do_nothing(index_elements=[DeletionTombstoneRecord.event_id])
        .returning(DeletionTombstoneRecord.event_id)
    )
    # A concurrent replay may have inserted the same event after our initial read.
    return inserted is None
