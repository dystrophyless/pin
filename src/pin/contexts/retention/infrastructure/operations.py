"""Redacted, read-only operational projection for retained deletion work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.retention.infrastructure.models import (
    DeletionRequestRecord,
    DeletionTombstoneRecord,
)

MAX_OPERATIONAL_IDS = 25
DELETION_STATES = ("previewed", "cancelled", "pending_export", "completed")


@dataclass(frozen=True, slots=True)
class OperationalDeletionProgress:
    request_state_counts: dict[str, int]
    oldest_pending_seconds: float | None
    pending_request_ids: tuple[str, ...]
    unexported_tombstone_count: int
    unexported_tombstone_event_ids: tuple[str, ...]
    restore_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_state_counts": self.request_state_counts,
            "oldest_pending_seconds": self.oldest_pending_seconds,
            "pending_request_ids": self.pending_request_ids,
            "unexported_tombstone_count": self.unexported_tombstone_count,
            "unexported_tombstone_event_ids": self.unexported_tombstone_event_ids,
            "restore_status": self.restore_status,
        }


async def read_operational_progress(session: AsyncSession) -> OperationalDeletionProgress:
    now = datetime.now(UTC)
    state_rows = await session.execute(
        select(DeletionRequestRecord.state, func.count())
        .group_by(DeletionRequestRecord.state)
        .order_by(DeletionRequestRecord.state)
    )
    counts = dict.fromkeys(DELETION_STATES, 0)
    counts.update({state: int(count) for state, count in state_rows if state in counts})

    oldest_pending = await session.scalar(
        select(func.min(DeletionRequestRecord.confirmed_at)).where(
            DeletionRequestRecord.state == "pending_export",
            DeletionRequestRecord.confirmed_at.is_not(None),
        )
    )
    pending_ids = tuple(
        str(request_id)
        for request_id in await session.scalars(
            select(DeletionRequestRecord.id)
            .where(DeletionRequestRecord.state == "pending_export")
            .order_by(DeletionRequestRecord.confirmed_at, DeletionRequestRecord.id)
            .limit(MAX_OPERATIONAL_IDS)
        )
    )
    unexported_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DeletionTombstoneRecord)
            .where(DeletionTombstoneRecord.exported_at.is_(None))
        )
        or 0
    )
    event_ids = tuple(
        str(event_id)
        for event_id in await session.scalars(
            select(DeletionTombstoneRecord.event_id)
            .where(DeletionTombstoneRecord.exported_at.is_(None))
            .order_by(DeletionTombstoneRecord.deleted_at, DeletionTombstoneRecord.event_id)
            .limit(MAX_OPERATIONAL_IDS)
        )
    )
    age = (now - oldest_pending).total_seconds() if oldest_pending is not None else None
    return OperationalDeletionProgress(
        request_state_counts=counts,
        oldest_pending_seconds=max(0.0, age) if age is not None else None,
        pending_request_ids=pending_ids,
        unexported_tombstone_count=unexported_count,
        unexported_tombstone_event_ids=event_ids,
        restore_status="unavailable",
    )
