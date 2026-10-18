"""Owner-scoped, read-only Planning facts for mutation interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.application.schedule_snapshot import PersistedReminderSchedule
from pin.contexts.planning.domain.plans import OccurrenceState, ResolvedSchedule
from pin.contexts.planning.infrastructure.mutation_read_store import (
    SqlAlchemyMutationTargetReader,
)


@dataclass(frozen=True, slots=True)
class MutationTargetSummary:
    """Current open owned occurrence facts safe to include in model context."""

    owner_id: UUID
    occurrence_id: UUID
    occurrence_version: int
    plan_item_id: UUID
    plan_version: int
    series_id: UUID | None
    series_version: int | None
    state: OccurrenceState
    title: str
    schedule: ResolvedSchedule
    reminders: tuple[PersistedReminderSchedule, ...] = ()


@dataclass(frozen=True, slots=True)
class MutationTargetCursor:
    local_date: date
    local_time: time | None
    occurrence_id: UUID


@dataclass(frozen=True, slots=True)
class MutationTargetPage:
    items: tuple[MutationTargetSummary, ...]
    next_cursor: MutationTargetCursor | None


async def get_owner_mutation_targets(
    session: AsyncSession,
    *,
    owner_id: UUID,
    at: datetime,
    limit: int = 50,
) -> tuple[MutationTargetSummary, ...]:
    """Return a bounded deterministic snapshot of current open owned targets.

    `at` is part of the caller's interpretation snapshot contract. Past open
    targets remain visible so completion/cancellation can still be offered;
    mutation preview applies the action-specific current-access rules.
    """
    if owner_id.int == 0:
        raise ValueError("owner_id is required")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    return await SqlAlchemyMutationTargetReader(session).get_owner_targets(
        owner_id=owner_id,
        limit=limit,
    )


async def get_owner_mutation_target(
    session: AsyncSession,
    *,
    owner_id: UUID,
    occurrence_id: UUID,
    at: datetime,
) -> MutationTargetSummary | None:
    """Read one current open target by its owner-scoped occurrence identity."""
    if owner_id.int == 0 or occurrence_id.int == 0:
        raise ValueError("owner and occurrence IDs are required")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    return await SqlAlchemyMutationTargetReader(session).get_owner_target(
        owner_id=owner_id,
        occurrence_id=occurrence_id,
    )


async def get_owner_mutation_target_page(
    session: AsyncSession,
    *,
    owner_id: UUID,
    at: datetime,
    after: MutationTargetCursor | None = None,
    limit: int = 10,
) -> MutationTargetPage:
    """Read one deterministic keyset page of all open targets for a user menu."""
    if owner_id.int == 0:
        raise ValueError("owner_id is required")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    items, next_cursor = await SqlAlchemyMutationTargetReader(session).get_owner_target_page(
        owner_id=owner_id,
        after=after,
        limit=limit,
    )
    return MutationTargetPage(items, next_cursor)


__all__ = [
    "MutationTargetCursor",
    "MutationTargetPage",
    "MutationTargetSummary",
    "get_owner_mutation_target_page",
    "get_owner_mutation_target",
    "get_owner_mutation_targets",
]
