"""Caller-UoW SQL adapter for owner-scoped mutation interpretation snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.domain.plans import (
    OccurrenceState,
    PlanKind,
    ResolvedSchedule,
)
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)

if TYPE_CHECKING:
    from pin.contexts.planning.mutation_read_api import MutationTargetCursor, MutationTargetSummary


class SqlAlchemyMutationTargetReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owner_targets(
        self, *, owner_id: UUID, limit: int
    ) -> tuple[MutationTargetSummary, ...]:
        items, _ = await self._query_owner_targets(owner_id=owner_id, limit=limit)
        return items

    async def get_owner_target(
        self, *, owner_id: UUID, occurrence_id: UUID
    ) -> MutationTargetSummary | None:
        items, _ = await self._query_owner_targets(
            owner_id=owner_id, occurrence_id=occurrence_id, limit=1
        )
        return items[0] if items else None

    async def get_owner_target_page(
        self,
        *,
        owner_id: UUID,
        after: MutationTargetCursor | None,
        limit: int,
    ) -> tuple[tuple[MutationTargetSummary, ...], MutationTargetCursor | None]:
        from pin.contexts.planning.mutation_read_api import MutationTargetCursor

        items, has_more = await self._query_owner_targets(
            owner_id=owner_id, limit=limit + 1, after=after
        )
        if not has_more and len(items) <= limit:
            return items, None
        visible = items[:limit]
        if not visible:
            return (), None
        last = visible[-1]
        return (
            visible,
            MutationTargetCursor(
                local_date=last.schedule.local_date,
                local_time=last.schedule.local_time,
                occurrence_id=last.occurrence_id,
            ),
        )

    async def _query_owner_targets(
        self,
        *,
        owner_id: UUID,
        limit: int,
        after: MutationTargetCursor | None = None,
        occurrence_id: UUID | None = None,
    ) -> tuple[tuple[MutationTargetSummary, ...], bool]:
        from pin.contexts.planning.mutation_read_api import MutationTargetSummary

        statement = (
            select(
                OccurrenceRecord,
                PlanItemRecord,
                SeriesOccurrenceRecord,
                SeriesRecord,
            )
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == OccurrenceRecord.plan_item_id)
                & (PlanItemRecord.owner_id == OccurrenceRecord.owner_id),
            )
            .outerjoin(
                SeriesOccurrenceRecord,
                (SeriesOccurrenceRecord.occurrence_id == OccurrenceRecord.id)
                & (SeriesOccurrenceRecord.owner_id == OccurrenceRecord.owner_id),
            )
            .outerjoin(
                SeriesRecord,
                (SeriesRecord.id == SeriesOccurrenceRecord.series_id)
                & (SeriesRecord.owner_id == SeriesOccurrenceRecord.owner_id),
            )
            .where(
                OccurrenceRecord.owner_id == owner_id,
                PlanItemRecord.owner_id == owner_id,
                OccurrenceRecord.state == OccurrenceState.OPEN.value,
            )
        )
        if occurrence_id is not None:
            statement = statement.where(OccurrenceRecord.id == occurrence_id)
        if after is not None:
            same_day = OccurrenceRecord.local_date == after.local_date
            if after.local_time is None:
                after_same_day = and_(
                    same_day,
                    OccurrenceRecord.local_time.is_(None),
                    OccurrenceRecord.id > after.occurrence_id,
                )
            else:
                after_same_day = and_(
                    same_day,
                    or_(
                        OccurrenceRecord.local_time > after.local_time,
                        OccurrenceRecord.local_time.is_(None),
                        and_(
                            OccurrenceRecord.local_time == after.local_time,
                            OccurrenceRecord.id > after.occurrence_id,
                        ),
                    ),
                )
            statement = statement.where(
                or_(OccurrenceRecord.local_date > after.local_date, after_same_day)
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    OccurrenceRecord.local_date,
                    OccurrenceRecord.local_time.asc().nulls_last(),
                    OccurrenceRecord.id,
                )
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        ).all()
        result: list[MutationTargetSummary] = []
        for occurrence, item, link, series in rows:
            result.append(
                MutationTargetSummary(
                    owner_id=owner_id,
                    occurrence_id=occurrence.id,
                    occurrence_version=occurrence.version,
                    plan_item_id=item.id,
                    plan_version=item.version,
                    series_id=link.series_id if link is not None else None,
                    series_version=series.version if series is not None else None,
                    state=OccurrenceState(occurrence.state),
                    title=item.title,
                    schedule=ResolvedSchedule(
                        kind=PlanKind(occurrence.kind),
                        event_zone=occurrence.event_zone,
                        local_date=occurrence.local_date,
                        local_time=occurrence.local_time,
                        fold=occurrence.fold,
                        end_local_date=occurrence.end_local_date,
                        end_local_time=occurrence.end_local_time,
                        end_fold=occurrence.end_fold,
                        starts_at=occurrence.starts_at,
                        ends_at=occurrence.ends_at,
                        deadline_at=occurrence.deadline_at,
                        duration_seconds=occurrence.duration_seconds,
                    ),
                )
            )
        return tuple(result), len(rows) == limit


__all__ = ["SqlAlchemyMutationTargetReader"]
