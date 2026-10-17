from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.application.accountability_reads import (
    AccountabilityOccurrence,
    AccountabilityOccurrencePage,
)
from pin.contexts.planning.application.agenda import AgendaView, agenda_window
from pin.contexts.planning.infrastructure.models import OccurrenceRecord, PlanItemRecord


class SqlAlchemyAccountabilityReadStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_accountability_occurrence(
        self, *, owner_id: UUID, occurrence_id: UUID, for_update: bool = False
    ) -> AccountabilityOccurrence | None:
        occurrence = OccurrenceRecord
        statement = (
            select(occurrence, PlanItemRecord.title, PlanItemRecord.created_at)
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == occurrence.plan_item_id)
                & (PlanItemRecord.owner_id == occurrence.owner_id),
            )
            .where(occurrence.owner_id == owner_id, occurrence.id == occurrence_id)
        )
        if for_update:
            plan_item_id = await self._session.scalar(
                select(occurrence.plan_item_id).where(
                    occurrence.owner_id == owner_id, occurrence.id == occurrence_id
                )
            )
            if plan_item_id is None:
                return None
            await self._session.execute(
                select(PlanItemRecord.id)
                .where(PlanItemRecord.owner_id == owner_id, PlanItemRecord.id == plan_item_id)
                .order_by(PlanItemRecord.id)
                .with_for_update(of=PlanItemRecord)
            )
            await self._session.execute(
                select(occurrence.id)
                .where(occurrence.owner_id == owner_id, occurrence.id == occurrence_id)
                .order_by(occurrence.id)
                .with_for_update(of=occurrence)
            )
            statement = statement.execution_options(populate_existing=True)
        row = (await self._session.execute(statement)).one_or_none()
        return _occurrence(row[0], row[1], row[2]) if row is not None else None

    async def list_today_open_untimed(
        self,
        *,
        owner_id: UUID,
        profile_zone: str,
        at: datetime,
        after_occurrence_id: UUID | None = None,
        limit: int = 100,
        for_update: bool = False,
    ) -> AccountabilityOccurrencePage:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("accountability read clock must be timezone-aware")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("accountability read limit must be between 1 and 1000")
        today = agenda_window(AgendaView.TODAY, profile_zone, at).start_date
        occurrence = OccurrenceRecord
        statement = (
            select(occurrence, PlanItemRecord.title, PlanItemRecord.created_at)
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == occurrence.plan_item_id)
                & (PlanItemRecord.owner_id == occurrence.owner_id),
            )
            .where(
                occurrence.owner_id == owner_id,
                occurrence.kind == "untimed",
                occurrence.state == "open",
                occurrence.local_date == today,
            )
            .order_by(occurrence.id)
            .limit(limit + 1)
        )
        if after_occurrence_id is not None:
            statement = statement.where(occurrence.id > after_occurrence_id)
        membership: Sequence[Row[tuple[UUID, UUID]]] = ()
        if for_update:
            membership = (
                await self._session.execute(
                    statement.with_only_columns(occurrence.id, occurrence.plan_item_id)
                )
            ).all()
            if not membership:
                return AccountabilityOccurrencePage((), None)
            occurrence_ids = tuple(row.id for row in membership)
            plan_item_ids = tuple(sorted({row.plan_item_id for row in membership}))
            await self._session.execute(
                select(PlanItemRecord.id)
                .where(
                    PlanItemRecord.owner_id == owner_id,
                    PlanItemRecord.id.in_(plan_item_ids),
                )
                .order_by(PlanItemRecord.id)
                .with_for_update(of=PlanItemRecord)
            )
            await self._session.execute(
                select(occurrence.id)
                .where(occurrence.owner_id == owner_id, occurrence.id.in_(occurrence_ids))
                .order_by(occurrence.id)
                .with_for_update(of=occurrence)
            )
            statement = statement.where(occurrence.id.in_(occurrence_ids)).execution_options(
                populate_existing=True
            )
        rows = (await self._session.execute(statement)).all()
        has_more = len(rows) > limit or (for_update and len(membership) > len(rows))
        page_rows = rows[:limit]
        next_occurrence_id = page_rows[-1][0].id if page_rows else None
        if has_more and next_occurrence_id is None and membership:
            next_occurrence_id = membership[-1].id
        return AccountabilityOccurrencePage(
            items=tuple(_occurrence(row[0], row[1], row[2]) for row in page_rows),
            next_occurrence_id=next_occurrence_id if has_more else None,
        )


def _occurrence(
    record: OccurrenceRecord, title: str, plan_created_at: datetime
) -> AccountabilityOccurrence:
    return AccountabilityOccurrence(
        owner_id=record.owner_id,
        occurrence_id=record.id,
        occurrence_version=record.version,
        kind=record.kind,
        state=record.state,
        title=title,
        event_zone=record.event_zone,
        local_date=record.local_date,
        starts_at=record.starts_at.astimezone(UTC) if record.starts_at is not None else None,
        ends_at=record.ends_at.astimezone(UTC) if record.ends_at is not None else None,
        deadline_at=record.deadline_at.astimezone(UTC) if record.deadline_at is not None else None,
        source_created_at=min(record.created_at, plan_created_at).astimezone(UTC),
    )
