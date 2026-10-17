"""Locked read adapter exposing Planning's current durable reminder schedule."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.identity.api import IdentityStore
from pin.contexts.notifications.api import (
    CurrentOccurrenceSchedule,
    CurrentPlanSchedule,
    CurrentScheduleStore,
    ReminderCandidate,
    ScheduleChange,
)
from pin.contexts.planning.application.schedule_snapshot import (
    PersistedReminderSchedule,
    PlanningOccurrenceSchedule,
    PlanningOwnerOccurrence,
    PlanningOwnerSchedulePage,
    PlanningScheduleChange,
    PlanningScheduleSnapshot,
    PlanningScheduleSnapshotStore,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    PlanKind,
    ReminderOffset,
    ResolvedSchedule,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesOccurrenceRecord,
    SeriesRecord,
)

_SERIES_EVENTS = {
    "planning.series_created",
    "planning.series_generated",
    "planning.series_exception_applied",
    "planning.series_stopped",
    "planning.series_approval_updated",
}
_OCCURRENCE_SERIES_EVENTS = {
    "planning.plan_rescheduled",
    "planning.occurrence_completed",
    "planning.occurrence_cancelled",
    "planning.series_occurrence_created",
    "planning.series_occurrence_rescheduled",
}


class SqlAlchemyPlanningScheduleSnapshotStore(PlanningScheduleSnapshotStore):
    """Reads schedules through the caller's session and leaves transaction ownership outside."""

    def __init__(self, session: AsyncSession, identity: IdentityStore) -> None:
        self._session = session
        self._identity = identity

    async def list_current_owner_schedules(
        self,
        owner_id: UUID,
        *,
        after_occurrence_id: UUID | None,
        limit: int,
    ) -> PlanningOwnerSchedulePage:
        """Read one bounded page without taking a roster-wide lock wave."""
        if not 1 <= limit <= 250:
            raise ValueError("schedule page limit must be between 1 and 250")
        statement = (
            select(OccurrenceRecord, PlanItemRecord)
            .join(
                PlanItemRecord,
                (PlanItemRecord.id == OccurrenceRecord.plan_item_id)
                & (PlanItemRecord.owner_id == OccurrenceRecord.owner_id),
            )
            .where(
                OccurrenceRecord.owner_id == owner_id,
                OccurrenceRecord.state == "open",
            )
            .order_by(OccurrenceRecord.id)
            .limit(limit + 1)
        )
        if after_occurrence_id is not None:
            statement = statement.where(OccurrenceRecord.id > after_occurrence_id)
        rows = list((await self._session.execute(statement)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        occurrence_ids = [occurrence.id for occurrence, _ in rows]
        if not occurrence_ids:
            return PlanningOwnerSchedulePage((), None)

        links = list(
            (
                await self._session.scalars(
                    select(SeriesOccurrenceRecord).where(
                        SeriesOccurrenceRecord.owner_id == owner_id,
                        SeriesOccurrenceRecord.occurrence_id.in_(occurrence_ids),
                    )
                )
            ).all()
        )
        series_ids = {link.series_id for link in links}
        series_rows = (
            list(
                (
                    await self._session.scalars(
                        select(SeriesRecord).where(
                            SeriesRecord.owner_id == owner_id,
                            SeriesRecord.id.in_(series_ids),
                        )
                    )
                ).all()
            )
            if series_ids
            else []
        )
        links_by_occurrence = {link.occurrence_id: link for link in links}
        series_by_id = {series.id: series for series in series_rows}
        reminder_rows = list(
            (
                await self._session.scalars(
                    select(ReminderRuleRecord)
                    .where(
                        ReminderRuleRecord.owner_id == owner_id,
                        ReminderRuleRecord.occurrence_id.in_(occurrence_ids),
                    )
                    .order_by(ReminderRuleRecord.occurrence_id, ReminderRuleRecord.due_at)
                )
            ).all()
        )
        reminders_by_occurrence: dict[UUID, list[PersistedReminderSchedule]] = {}
        for reminder in reminder_rows:
            reminders_by_occurrence.setdefault(reminder.occurrence_id, []).append(
                PersistedReminderSchedule(
                    ReminderOffset(
                        OffsetKind(reminder.offset_kind),
                        reminder.offset_amount,
                        reminder.source,
                    ),
                    reminder.due_at,
                )
            )

        items = []
        for occurrence, plan in rows:
            link = links_by_occurrence.get(occurrence.id)
            series = series_by_id.get(link.series_id) if link is not None else None
            current = PlanningOccurrenceSchedule(
                occurrence_id=occurrence.id,
                occurrence_version=occurrence.version,
                state=occurrence.state,
                approved_preferences_version=occurrence.reminder_preferences_version,
                schedule=_resolved_schedule(occurrence),
                reminders=tuple(reminders_by_occurrence.get(occurrence.id, ())),
                series_id=series.id if series is not None else None,
                series_version=series.version if series is not None else None,
                series_state=series.state if series is not None else None,
                source_created_at=min(
                    value for value in (plan.created_at, series.created_at if series else None)
                    if value is not None
                ),
            )
            items.append(
                PlanningOwnerOccurrence(
                    owner_id=owner_id,
                    plan_item_id=plan.id,
                    plan_version=plan.version,
                    preferences_version=max(1, occurrence.reminder_preferences_version or 1),
                    occurrence=current,
                )
            )
        next_cursor = rows[-1][0].id if has_more else None
        return PlanningOwnerSchedulePage(tuple(items), next_cursor)

    async def lock_schedule(
        self, change: PlanningScheduleChange
    ) -> PlanningScheduleSnapshot | None:
        preferences = await self._identity.lock_preferences(change.owner_id)
        if preferences is None or preferences.owner_id != change.owner_id:
            return None

        series_id = await self._series_id_for(change)
        series: SeriesRecord | None = None
        if series_id is not None:
            series = await self._session.scalar(
                select(SeriesRecord)
                .where(
                    SeriesRecord.id == series_id,
                    SeriesRecord.owner_id == change.owner_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if series is None:
                return None
            links = list(
                (
                    await self._session.scalars(
                        select(SeriesOccurrenceRecord)
                        .where(
                            SeriesOccurrenceRecord.owner_id == change.owner_id,
                            SeriesOccurrenceRecord.series_id == series.id,
                        )
                        .order_by(SeriesOccurrenceRecord.sequence)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            occurrence_ids = [link.occurrence_id for link in links]
            plan_item_id = series.plan_item_id
        else:
            occurrence_ids = [change.aggregate_id]
            occurrence = await self._session.scalar(
                select(OccurrenceRecord)
                .where(
                    OccurrenceRecord.id == change.aggregate_id,
                    OccurrenceRecord.owner_id == change.owner_id,
                )
                .execution_options(populate_existing=True)
            )
            if occurrence is None:
                return None
            plan_item_id = occurrence.plan_item_id

        item = await self._session.scalar(
            select(PlanItemRecord)
            .where(
                PlanItemRecord.id == plan_item_id,
                PlanItemRecord.owner_id == change.owner_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None:
            return None
        rows = list(
            (
                await self._session.scalars(
                    select(OccurrenceRecord)
                    .where(
                        OccurrenceRecord.owner_id == change.owner_id,
                        OccurrenceRecord.plan_item_id == plan_item_id,
                        OccurrenceRecord.id.in_(occurrence_ids),
                    )
                    .order_by(OccurrenceRecord.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        reminders = list(
            (
                await self._session.scalars(
                    select(ReminderRuleRecord)
                    .where(
                        ReminderRuleRecord.owner_id == change.owner_id,
                        ReminderRuleRecord.occurrence_id.in_([row.id for row in rows]),
                    )
                    .order_by(ReminderRuleRecord.occurrence_id, ReminderRuleRecord.due_at)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        by_occurrence: dict[object, list[PersistedReminderSchedule]] = {}
        for row in reminders:
            by_occurrence.setdefault(row.occurrence_id, []).append(
                PersistedReminderSchedule(
                    ReminderOffset(OffsetKind(row.offset_kind), row.offset_amount, row.source),
                    row.due_at,
                )
            )

        return PlanningScheduleSnapshot(
            owner_id=change.owner_id,
            plan_item_id=item.id,
            plan_version=item.version,
            preferences_version=preferences.version,
            occurrences=tuple(
                PlanningOccurrenceSchedule(
                    occurrence_id=row.id,
                    occurrence_version=row.version,
                    state=row.state,
                    approved_preferences_version=row.reminder_preferences_version,
                    schedule=_resolved_schedule(row),
                    reminders=tuple(by_occurrence.get(row.id, ())),
                    omitted=(),
                    series_id=series.id if series is not None else None,
                    series_version=series.version if series is not None else None,
                    series_state=series.state if series is not None else None,
                    source_created_at=min(
                        value
                        for value in (item.created_at, series.created_at if series else None)
                        if value is not None
                    ),
                )
                for row in rows
            ),
        )

    async def _series_id_for(self, change: PlanningScheduleChange) -> UUID | None:
        if change.event_type in _SERIES_EVENTS:
            return change.aggregate_id
        if change.event_type in _OCCURRENCE_SERIES_EVENTS:
            return cast(
                UUID | None,
                await self._session.scalar(
                    select(SeriesOccurrenceRecord.series_id).where(
                        SeriesOccurrenceRecord.owner_id == change.owner_id,
                        SeriesOccurrenceRecord.occurrence_id == change.aggregate_id,
                    )
                ),
            )
        return None


class SqlAlchemyReminderScheduleStore(CurrentScheduleStore):
    """Notifications adapter over Planning's published same-UoW snapshot port."""

    def __init__(self, session: AsyncSession, identity: IdentityStore) -> None:
        self._reader = SqlAlchemyPlanningScheduleSnapshotStore(session, identity)

    async def lock_current_schedule(self, change: ScheduleChange) -> CurrentPlanSchedule | None:
        snapshot = await self._reader.lock_schedule(
            PlanningScheduleChange(
                change.event_id,
                change.event_type,
                change.aggregate_id,
                change.aggregate_version,
                change.owner_id,
            )
        )
        if snapshot is None:
            return None
        return CurrentPlanSchedule(
            owner_id=snapshot.owner_id,
            plan_item_id=snapshot.plan_item_id,
            plan_version=snapshot.plan_version,
            preferences_version=snapshot.preferences_version,
            occurrences=tuple(
                CurrentOccurrenceSchedule(
                    occurrence_id=occurrence.occurrence_id,
                    occurrence_version=occurrence.occurrence_version,
                    state=occurrence.state,
                    approved_preferences_version=occurrence.approved_preferences_version,
                    event_start_at=(
                        occurrence.schedule.starts_at or occurrence.schedule.deadline_at
                    ),
                    reminders=tuple(
                        ReminderCandidate(
                            reminder.offset.kind.value,
                            reminder.offset.amount,
                            reminder.offset.source,
                            reminder.due_at,
                        )
                        for reminder in occurrence.reminders
                    ),
                    omitted=(),
                    series_id=occurrence.series_id,
                    series_version=occurrence.series_version,
                    series_state=occurrence.series_state,
                    source_created_at=occurrence.source_created_at,
                )
                for occurrence in snapshot.occurrences
            ),
        )


def _resolved_schedule(record: OccurrenceRecord) -> ResolvedSchedule:
    return ResolvedSchedule(
        kind=PlanKind(record.kind),
        event_zone=record.event_zone,
        local_date=record.local_date,
        local_time=record.local_time,
        fold=record.fold,
        end_local_date=record.end_local_date,
        end_local_time=record.end_local_time,
        end_fold=record.end_fold,
        starts_at=record.starts_at,
        ends_at=record.ends_at,
        deadline_at=record.deadline_at,
        duration_seconds=record.duration_seconds,
    )
