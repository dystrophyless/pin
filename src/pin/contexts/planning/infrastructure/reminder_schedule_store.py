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
                )
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
