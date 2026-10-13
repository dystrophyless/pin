"""Caller-owned PostgreSQL unit-of-work adapter for recurrence series."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceCommandStore,
    SeriesOccurrenceSnapshot,
    SeriesSnapshot,
)
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    PlanKind,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ResolvedSchedule,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceFrequency,
    RecurrenceOccurrence,
    RecurrenceProjection,
    RecurrenceRule,
    RecurringPlanDraft,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.contexts.planning.infrastructure.series_models import (
    SeriesExceptionRecord,
    SeriesOccurrenceRecord,
    SeriesRecord,
)
from pin.persistence.receipts import find_action_receipt, lock_action, record_action_receipt
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event


class SeriesNotFound(LookupError):
    """The requested series does not belong to the authenticated owner."""


class SeriesVersionConflict(ValueError):
    """The recurrence rule changed after the generation/edit preview."""


class SeriesStopped(ValueError):
    """A stopped series cannot generate new occurrences."""


class SqlAlchemySeriesStore(RecurrenceCommandStore):
    """Writes recurrence records, ordinary PlanItems, receipts and events atomically."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_action(self, owner_id: UUID, action_key: str) -> None:
        await lock_action(self._session, owner_id, action_key)

    async def get_receipt(self, owner_id: UUID, action_key: str) -> dict[str, object] | None:
        receipt = await find_action_receipt(self._session, owner_id, action_key)
        return dict(receipt.result) if receipt is not None else None

    async def persist_series(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        approved_preferences_version: int,
        draft: RecurringPlanDraft,
        projection: RecurrenceProjection,
        effective_reminders: tuple[ReminderOffset, ...],
        default_zone: str,
        quiet_zone: str,
        quiet_start: time,
        quiet_end: time,
        at: datetime,
        horizon_date: date,
    ) -> dict[str, object]:
        if not projection.occurrences:
            raise ValueError("a confirmed recurrence must project at least one occurrence")
        plan_item_id = uuid4()
        series_id = uuid4()
        self._session.add(
            PlanItemRecord(
                id=plan_item_id,
                owner_id=owner_id,
                kind=draft.kind.value,
                title=draft.title.strip(),
                version=1,
            )
        )
        await self._session.flush()
        series = SeriesRecord(
            id=series_id,
            owner_id=owner_id,
            plan_item_id=plan_item_id,
            version=1,
            state="active",
            anchor_date=draft.start_date,
            anchor_sequence=0,
            next_sequence=0,
            total_count=draft.recurrence.count,
            end_date=draft.recurrence.end_date,
            rule=_rule_payload(draft.recurrence),
            typed_draft=_draft_payload(
                draft,
                effective_reminders,
                default_zone,
                approved_preferences_version=approved_preferences_version,
                quiet_zone=quiet_zone,
                quiet_start=quiet_start,
                quiet_end=quiet_end,
            ),
        )
        self._session.add(series)
        await self._session.flush()
        committed: list[dict[str, object]] = []
        for occurrence in projection.occurrences:
            record = _new_occurrence(
                owner_id=owner_id,
                plan_item_id=plan_item_id,
                occurrence=occurrence,
                approved_preferences_version=approved_preferences_version,
            )
            self._session.add(record)
            await self._session.flush()
            self._session.add(
                SeriesOccurrenceRecord(
                    owner_id=owner_id,
                    series_id=series_id,
                    occurrence_id=record.id,
                    sequence=occurrence.sequence,
                    recurrence_key=occurrence.recurrence_key,
                    dst_gap_shift_seconds=occurrence.dst_gap_shift_seconds,
                    repeated_time_choice=occurrence.repeated_time_choice,
                )
            )
            await self._add_reminders(owner_id, record.id, occurrence)
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_occurrence_created",
                    schema_version=1,
                    aggregate_id=record.id,
                    aggregate_version=1,
                    owner_id=owner_id,
                    occurred_at=at.astimezone(UTC),
                    payload={
                        "series_id": str(series_id),
                        "plan_item_id": str(plan_item_id),
                        "occurrence_id": str(record.id),
                        "recurrence_key": occurrence.recurrence_key,
                        "sequence": occurrence.sequence,
                        "schedule": _schedule_payload(occurrence),
                        "reminders": _reminders_payload(occurrence),
                        "omitted_reminders": [
                            _omission_payload(item) for item in occurrence.omitted_reminders
                        ],
                        "reminder_notices": [
                            _notice_payload(item) for item in occurrence.reminder_notices
                        ],
                    },
                ),
            )
            committed.append(
                {
                    "occurrence_id": str(record.id),
                    "recurrence_key": occurrence.recurrence_key,
                    "sequence": occurrence.sequence,
                    "local_date": occurrence.schedule.local_date.isoformat(),
                    "omitted_reminders": [
                        _omission_payload(item) for item in occurrence.omitted_reminders
                    ],
                    "reminder_notices": [
                        _notice_payload(item) for item in occurrence.reminder_notices
                    ],
                    "dst_gap_shift_seconds": occurrence.dst_gap_shift_seconds,
                    "repeated_time_choice": occurrence.repeated_time_choice,
                }
            )
        series.next_sequence = len(projection.occurrences)
        await add_outbox_event(
            self._session,
            _series_event(
                owner_id,
                series_id,
                version=1,
                at=at,
                event_type="planning.series_created",
                payload={
                    "plan_item_id": str(plan_item_id),
                    "version": 1,
                    "frequency": draft.recurrence.frequency.value,
                    "interval": draft.recurrence.interval,
                    "anchor_date": draft.start_date.isoformat(),
                    "horizon_date": horizon_date.isoformat(),
                    "generation_truncated": projection.truncated,
                },
            )
        )
        result: dict[str, object] = {
            "payload_sha256": payload_sha256,
            "series_id": str(series_id),
            "plan_item_id": str(plan_item_id),
            "series_version": 1,
            "occurrences": committed,
            "generation_truncated": projection.truncated,
            "horizon_date": horizon_date.isoformat(),
            "committed_at": at.astimezone(UTC).isoformat(),
        }
        await record_action_receipt(self._session, owner_id, action_key, result)
        await self._session.flush()
        return result

    async def generate_occurrences(
        self,
        *,
        owner_id: UUID,
        series_id: UUID,
        expected_version: int,
        through: date,
        at: datetime,
        projection: RecurrenceProjection,
        approved_preferences_version: int,
        max_occurrences: int = 1000,
    ) -> dict[str, object]:
        """Advance one locked generation cursor; retries cannot duplicate recurrence keys."""
        series = await self._session.scalar(
            select(SeriesRecord)
            .where(SeriesRecord.id == series_id, SeriesRecord.owner_id == owner_id)
            .with_for_update()
        )
        if series is None:
            raise SeriesNotFound("series is missing or belongs to another owner")
        if series.version != expected_version:
            raise SeriesVersionConflict("series changed after generation request")
        if series.state != "active":
            raise SeriesStopped("a stopped recurrence series cannot generate occurrences")
        if max_occurrences < 1 or max_occurrences > 1000:
            raise ValueError("generation batch must contain 1 to 1000 occurrences")
        item = await self._session.scalar(
            select(PlanItemRecord).where(
                PlanItemRecord.id == series.plan_item_id,
                PlanItemRecord.owner_id == owner_id,
            )
        )
        if item is None:
            raise SeriesNotFound("series plan item is missing")
        if len(projection.occurrences) > max_occurrences or max_occurrences > 1000:
            raise ValueError("generation projection exceeds the requested batch bound")
        if any(item.sequence < series.next_sequence for item in projection.occurrences):
            raise ValueError("generation projection overlaps the persisted cursor")
        committed: list[dict[str, object]] = []
        for occurrence in projection.occurrences:
            # The lock fences concurrent generators; the database uniqueness key
            # remains the final defense against retries or repair races.
            exists = await self._session.scalar(
                select(SeriesOccurrenceRecord.id).where(
                    SeriesOccurrenceRecord.series_id == series_id,
                    SeriesOccurrenceRecord.recurrence_key == occurrence.recurrence_key,
                )
            )
            if exists is not None:
                continue
            record = _new_occurrence(
                owner_id=owner_id,
                plan_item_id=item.id,
                occurrence=occurrence,
                approved_preferences_version=approved_preferences_version,
            )
            self._session.add(record)
            await self._session.flush()
            self._session.add(
                SeriesOccurrenceRecord(
                    owner_id=owner_id,
                    series_id=series_id,
                    occurrence_id=record.id,
                    sequence=occurrence.sequence,
                    recurrence_key=occurrence.recurrence_key,
                    dst_gap_shift_seconds=occurrence.dst_gap_shift_seconds,
                    repeated_time_choice=occurrence.repeated_time_choice,
                )
            )
            await self._add_reminders(owner_id, record.id, occurrence)
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_occurrence_created",
                    schema_version=1,
                    aggregate_id=record.id,
                    aggregate_version=1,
                    owner_id=owner_id,
                    occurred_at=at.astimezone(UTC),
                    payload={
                        "series_id": str(series_id),
                        "plan_item_id": str(item.id),
                        "occurrence_id": str(record.id),
                        "recurrence_key": occurrence.recurrence_key,
                        "sequence": occurrence.sequence,
                        "schedule": _schedule_payload(occurrence),
                        "reminders": _reminders_payload(occurrence),
                        "omitted_reminders": [
                            _omission_payload(item) for item in occurrence.omitted_reminders
                        ],
                        "reminder_notices": [
                            _notice_payload(item) for item in occurrence.reminder_notices
                        ],
                    },
                ),
            )
            committed.append(
                {
                    "occurrence_id": str(record.id),
                    "recurrence_key": occurrence.recurrence_key,
                    "sequence": occurrence.sequence,
                    "local_date": occurrence.schedule.local_date.isoformat(),
                    "omitted_reminders": [
                        _omission_payload(item) for item in occurrence.omitted_reminders
                    ],
                    "reminder_notices": [
                        _notice_payload(item) for item in occurrence.reminder_notices
                    ],
                }
            )
        series.next_sequence += len(projection.occurrences)
        if committed:
            series.version += 1
            await add_outbox_event(
                self._session,
                _series_event(
                    owner_id,
                    series_id,
                    version=series.version,
                    at=at,
                    event_type="planning.series_generated",
                    payload={
                        "through": through.isoformat(),
                        "occurrences": committed,
                        "generation_truncated": projection.truncated,
                    },
                ),
            )
        await self._session.flush()
        return {
            "series_id": str(series_id),
            "series_version": series.version,
            "occurrences": committed,
            "next_sequence": series.next_sequence,
            "generation_truncated": projection.truncated,
        }

    async def get_series_snapshot(
        self, owner_id: UUID, series_id: UUID
    ) -> SeriesSnapshot | None:
        series = await self._session.scalar(
            select(SeriesRecord).where(
                SeriesRecord.id == series_id,
                SeriesRecord.owner_id == owner_id,
            )
        )
        return await self._snapshot(series) if series is not None else None

    async def lock_series_snapshot(
        self, owner_id: UUID, series_id: UUID
    ) -> SeriesSnapshot | None:
        series = await self._session.scalar(
            select(SeriesRecord)
            .where(SeriesRecord.id == series_id, SeriesRecord.owner_id == owner_id)
            .with_for_update()
        )
        return await self._snapshot(series) if series is not None else None

    async def apply_exception(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        snapshot: SeriesSnapshot,
        change: RecurrenceExceptionDraft,
        projection: RecurrenceProjection,
        new_draft: RecurringPlanDraft,
        approved_preferences_version: int,
        quiet_zone: str,
        quiet_start: time,
        quiet_end: time,
        approval_projection_sha256: str,
        at: datetime,
    ) -> dict[str, object]:
        series = await self._session.scalar(
            select(SeriesRecord)
            .where(
                SeriesRecord.id == snapshot.id,
                SeriesRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if series is None:
            raise SeriesNotFound("series is missing or belongs to another owner")
        if series.version != snapshot.version:
            raise SeriesVersionConflict("series changed after exception preview")
        if series.state != "active":
            raise SeriesStopped("a stopped series cannot be rescheduled")
        projected = {item.sequence: item for item in projection.occurrences}
        projection_by_sequence = {item.sequence: item for item in snapshot.occurrences}
        target = projection_by_sequence.get(change.sequence)
        if target is None or target.state != "open":
            raise SeriesVersionConflict("terminal or missing history cannot be reopened")
        if change.scope is RecurrenceExceptionScope.SINGLE:
            rows = [
                row
                for row in snapshot.occurrences
                if row.sequence == change.sequence and row.state == "open"
            ]
        else:
            rows = [
                row
                for row in snapshot.occurrences
                if row.sequence >= change.sequence and row.state == "open"
            ]
        if any(row.sequence not in projected for row in rows):
            raise ValueError(
                "approved exception projection does not cover stored future occurrences"
            )
        locked_rows: list[
            tuple[SeriesOccurrenceSnapshot, SeriesOccurrenceRecord, OccurrenceRecord]
        ] = []
        for old in sorted(rows, key=lambda item: item.sequence):
            association = await self._session.scalar(
                select(SeriesOccurrenceRecord)
                .where(
                    SeriesOccurrenceRecord.owner_id == owner_id,
                    SeriesOccurrenceRecord.series_id == snapshot.id,
                    SeriesOccurrenceRecord.sequence == old.sequence,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            record = await self._session.scalar(
                select(OccurrenceRecord)
                .where(
                    OccurrenceRecord.id == old.occurrence_id,
                    OccurrenceRecord.owner_id == owner_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if association is None or record is None:
                raise SeriesNotFound("occurrence identity link is missing")
            if (
                association.occurrence_id != old.occurrence_id
                or association.recurrence_key != old.recurrence_key
            ):
                raise SeriesVersionConflict("occurrence identity changed after exception preview")
            if record.state != "open" or record.state != old.state or record.version != old.version:
                raise SeriesVersionConflict("occurrence changed after exception preview")
            locked_rows.append((old, association, record))

        existing = await self._session.scalar(
            select(SeriesExceptionRecord)
            .where(
                SeriesExceptionRecord.series_id == snapshot.id,
                SeriesExceptionRecord.sequence == change.sequence,
            )
            .order_by(SeriesExceptionRecord.version.desc())
            .limit(1)
        )
        exception_version = existing.version + 1 if existing is not None else 1
        self._session.add(
            SeriesExceptionRecord(
                owner_id=owner_id,
                series_id=snapshot.id,
                sequence=change.sequence,
                version=exception_version,
                scope=change.scope.value,
                approved_change=_exception_payload(
                    change, new_draft, projection, approval_projection_sha256
                ),
            )
        )
        updated_rows: list[dict[str, object]] = []
        for old, association, record in locked_rows:
            recurrence = projected[old.sequence]
            record.version += 1
            _apply_resolved_schedule(record, recurrence.schedule)
            record.reminder_preferences_version = approved_preferences_version
            association.dst_gap_shift_seconds = recurrence.dst_gap_shift_seconds
            association.repeated_time_choice = recurrence.repeated_time_choice
            await self._session.execute(
                delete(ReminderRuleRecord).where(
                    ReminderRuleRecord.owner_id == owner_id,
                    ReminderRuleRecord.occurrence_id == record.id,
                )
            )
            await self._add_reminders(owner_id, record.id, recurrence)
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.series_occurrence_rescheduled",
                    schema_version=1,
                    aggregate_id=record.id,
                    aggregate_version=record.version,
                    owner_id=owner_id,
                    occurred_at=at.astimezone(UTC),
                    payload={
                        "series_id": str(snapshot.id),
                        "recurrence_key": association.recurrence_key,
                        "sequence": association.sequence,
                        "schedule": _schedule_payload(recurrence),
                        "reminders": _reminders_payload(recurrence),
                        "omitted_reminders": [
                            _omission_payload(item) for item in recurrence.omitted_reminders
                        ],
                        "reminder_notices": [
                            _notice_payload(item) for item in recurrence.reminder_notices
                        ],
                        "scope": change.scope.value,
                    },
                ),
            )
            updated_rows.append(
                {
                    "occurrence_id": str(record.id),
                    "recurrence_key": association.recurrence_key,
                    "sequence": association.sequence,
                    "local_date": record.local_date.isoformat(),
                    "version": record.version,
                    "reminders": _reminders_payload(recurrence),
                    "omitted_reminders": [
                        _omission_payload(item) for item in recurrence.omitted_reminders
                    ],
                    "reminder_notices": [
                        _notice_payload(item) for item in recurrence.reminder_notices
                    ],
                }
            )
        if change.scope is RecurrenceExceptionScope.THIS_AND_FUTURE:
            series.anchor_date = change.replacement.local_date
            series.anchor_sequence = change.sequence
            series.rule = _rule_payload(new_draft.recurrence)
            effective_reminders = (
                snapshot.effective_reminders
                if new_draft.reminders is None
                else new_draft.reminders
            )
            series.typed_draft = _draft_payload(
                new_draft,
                effective_reminders,
                snapshot.default_zone,
                approved_preferences_version=approved_preferences_version,
                quiet_zone=quiet_zone,
                quiet_start=quiet_start,
                quiet_end=quiet_end,
            )
        series.version += 1
        remaining_count = (
            max(0, series.total_count - change.sequence)
            if series.total_count is not None
            else None
        )
        await add_outbox_event(
            self._session,
            _series_event(
                owner_id,
                snapshot.id,
                version=series.version,
                at=at,
                event_type="planning.series_exception_applied",
                payload={
                    "plan_item_id": str(snapshot.plan_item_id),
                    "scope": change.scope.value,
                    "sequence": change.sequence,
                    "anchor_date": series.anchor_date.isoformat(),
                    "anchor_sequence": series.anchor_sequence,
                    "remaining_count": remaining_count,
                    "updated_occurrences": updated_rows,
                },
            ),
        )
        result: dict[str, object] = {
            "payload_sha256": payload_sha256,
            "series_id": str(snapshot.id),
            "plan_item_id": str(snapshot.plan_item_id),
            "series_version": series.version,
            "exception_version": exception_version,
            "scope": change.scope.value,
            "sequence": change.sequence,
            "anchor_date": series.anchor_date.isoformat(),
            "anchor_sequence": series.anchor_sequence,
            "total_count": series.total_count,
            "remaining_count": remaining_count,
            "occurrences": updated_rows,
            "committed_at": at.astimezone(UTC).isoformat(),
        }
        await record_action_receipt(self._session, owner_id, action_key, result)
        await self._session.flush()
        return result

    async def stop_series(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        snapshot: SeriesSnapshot,
        at: datetime,
    ) -> dict[str, object]:
        series = await self._session.scalar(
            select(SeriesRecord)
            .where(
                SeriesRecord.id == snapshot.id,
                SeriesRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if series is None:
            raise SeriesNotFound("series is missing or belongs to another owner")
        if series.version != snapshot.version:
            raise SeriesVersionConflict("series changed after stop preview")
        if series.state != "active":
            raise SeriesStopped("series is already stopped")
        series.state = "stopped"
        series.version += 1
        result: dict[str, object] = {
            "payload_sha256": payload_sha256,
            "series_id": str(series.id),
            "plan_item_id": str(series.plan_item_id),
            "series_version": series.version,
            "state": series.state,
            "next_sequence": series.next_sequence,
            "committed_at": at.astimezone(UTC).isoformat(),
        }
        await add_outbox_event(
            self._session,
            _series_event(
                owner_id,
                series.id,
                version=series.version,
                at=at,
                event_type="planning.series_stopped",
                payload={
                    "plan_item_id": str(series.plan_item_id),
                    "state": series.state,
                    "next_sequence": series.next_sequence,
                },
            ),
        )
        await record_action_receipt(self._session, owner_id, action_key, result)
        await self._session.flush()
        return result

    async def _snapshot(self, series: SeriesRecord) -> SeriesSnapshot:
        draft, reminders, zone = _restore_draft(series.typed_draft)
        joined = await self._session.execute(
            select(SeriesOccurrenceRecord, OccurrenceRecord)
            .join(
                OccurrenceRecord,
                (OccurrenceRecord.id == SeriesOccurrenceRecord.occurrence_id)
                & (OccurrenceRecord.owner_id == SeriesOccurrenceRecord.owner_id),
            )
            .where(
                SeriesOccurrenceRecord.series_id == series.id,
                SeriesOccurrenceRecord.owner_id == series.owner_id,
            )
            .order_by(SeriesOccurrenceRecord.sequence)
        )
        occurrences = tuple(
            SeriesOccurrenceSnapshot(
                sequence=association.sequence,
                occurrence_id=occurrence.id,
                recurrence_key=association.recurrence_key,
                state=occurrence.state,
                version=occurrence.version,
                local_date=occurrence.local_date,
                anchor_at=occurrence.starts_at or occurrence.deadline_at,
            )
            for association, occurrence in joined.all()
        )
        return SeriesSnapshot(
            id=series.id,
            owner_id=series.owner_id,
            plan_item_id=series.plan_item_id,
            version=series.version,
            state=series.state,
            anchor_date=series.anchor_date,
            anchor_sequence=series.anchor_sequence,
            next_sequence=series.next_sequence,
            total_count=series.total_count,
            end_date=series.end_date,
            draft=draft,
            effective_reminders=reminders,
            default_zone=zone,
            occurrences=occurrences,
            approved_preferences_version=(
                cast(int, series.typed_draft["approved_preferences_version"])
                if series.typed_draft.get("approved_preferences_version") is not None
                else None
            ),
            quiet_zone=(
                str(series.typed_draft["quiet_zone"])
                if series.typed_draft.get("quiet_zone")
                else None
            ),
            quiet_start=(
                time.fromisoformat(str(series.typed_draft["quiet_start"]))
                if series.typed_draft.get("quiet_start")
                else None
            ),
            quiet_end=(
                time.fromisoformat(str(series.typed_draft["quiet_end"]))
                if series.typed_draft.get("quiet_end")
                else None
            ),
        )

    async def _add_reminders(
        self, owner_id: UUID, occurrence_id: UUID, occurrence: RecurrenceOccurrence
    ) -> None:
        for offset, due_at in occurrence.reminders:
            self._session.add(
                ReminderRuleRecord(
                    owner_id=owner_id,
                    occurrence_id=occurrence_id,
                    offset_kind=offset.kind.value,
                    offset_amount=offset.amount,
                    source=offset.source,
                    due_at=due_at,
                )
            )


def _new_occurrence(
    *,
    owner_id: UUID,
    plan_item_id: UUID,
    occurrence: RecurrenceOccurrence,
    approved_preferences_version: int,
) -> OccurrenceRecord:
    schedule = occurrence.schedule
    return OccurrenceRecord(
        owner_id=owner_id,
        plan_item_id=plan_item_id,
        kind=schedule.kind.value,
        event_zone=schedule.event_zone,
        local_date=schedule.local_date,
        local_time=schedule.local_time,
        fold=schedule.fold,
        end_local_date=schedule.end_local_date,
        end_local_time=schedule.end_local_time,
        end_fold=schedule.end_fold,
        starts_at=schedule.starts_at,
        ends_at=schedule.ends_at,
        deadline_at=schedule.deadline_at,
        duration_seconds=schedule.duration_seconds,
        state="open",
        version=1,
        reminder_preferences_version=approved_preferences_version,
    )


def _apply_resolved_schedule(record: OccurrenceRecord, schedule: ResolvedSchedule) -> None:
    record.kind = schedule.kind.value
    record.event_zone = schedule.event_zone
    record.local_date = schedule.local_date
    record.local_time = schedule.local_time
    record.fold = schedule.fold
    record.end_local_date = schedule.end_local_date
    record.end_local_time = schedule.end_local_time
    record.end_fold = schedule.end_fold
    record.starts_at = schedule.starts_at
    record.ends_at = schedule.ends_at
    record.deadline_at = schedule.deadline_at
    record.duration_seconds = schedule.duration_seconds


def _exception_payload(
    change: RecurrenceExceptionDraft,
    new_draft: RecurringPlanDraft,
    projection: RecurrenceProjection,
    approval_projection_sha256: str,
) -> dict[str, object]:
    replacement = change.replacement
    return {
        "scope": change.scope.value,
        "sequence": change.sequence,
        "replacement": {
            "kind": replacement.kind.value,
            "title": replacement.title,
            "local_date": replacement.local_date.isoformat(),
            "local_time": replacement.local_time.isoformat()
            if replacement.local_time
            else None,
            "event_zone": replacement.event_zone,
            "end_local_date": replacement.end_local_date.isoformat()
            if replacement.end_local_date
            else None,
            "end_local_time": replacement.end_local_time.isoformat()
            if replacement.end_local_time
            else None,
            "duration_seconds": replacement.duration_seconds,
            "fold": replacement.fold,
            "end_fold": replacement.end_fold,
        },
        "recurrence": _rule_payload(new_draft.recurrence),
        "approved_projection_sha256": approval_projection_sha256,
        "approved_occurrences": [
            {
                "sequence": item.sequence,
                "recurrence_key": item.recurrence_key,
                "schedule": _schedule_payload(item),
                "reminders": _reminders_payload(item),
                "omitted_reminders": [
                    _omission_payload(omission) for omission in item.omitted_reminders
                ],
                "reminder_notices": [
                    _notice_payload(notice) for notice in item.reminder_notices
                ],
            }
            for item in projection.occurrences
        ],
    }


def _rule_payload(rule: RecurrenceRule) -> dict[str, object]:
    return {
        "frequency": rule.frequency.value,
        "interval": rule.interval,
        "weekdays": list(rule.weekdays),
        "monthly_day": rule.monthly_day,
        "last_day": rule.last_day,
        "count": rule.count,
        "end_date": rule.end_date.isoformat() if rule.end_date else None,
    }


def _draft_payload(
    draft: RecurringPlanDraft,
    reminders: tuple[ReminderOffset, ...],
    default_zone: str,
    *,
    approved_preferences_version: int,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
) -> dict[str, object]:
    return {
        "kind": draft.kind.value,
        "title": draft.title,
        "start_date": draft.start_date.isoformat(),
        "local_time": draft.local_time.isoformat() if draft.local_time else None,
        "event_zone": draft.event_zone,
        "duration_seconds": draft.duration_seconds,
        "end_local_time": draft.end_local_time.isoformat() if draft.end_local_time else None,
        "end_local_date": draft.end_local_date.isoformat() if draft.end_local_date else None,
        "fold": draft.fold,
        "end_fold": draft.end_fold,
        "reminders": None
        if draft.reminders is None
        else [_offset_payload(offset) for offset in draft.reminders],
        "effective_reminders": [_offset_payload(offset) for offset in reminders],
        "default_zone": default_zone,
        "approved_preferences_version": approved_preferences_version,
        "quiet_zone": quiet_zone,
        "quiet_start": quiet_start.isoformat(),
        "quiet_end": quiet_end.isoformat(),
        "recurrence": _rule_payload(draft.recurrence),
    }


def _restore_draft(
    value: dict[str, object],
) -> tuple[RecurringPlanDraft, tuple[ReminderOffset, ...], str]:
    raw_rule = cast(dict[str, object], value["recurrence"])
    end_date = date.fromisoformat(str(raw_rule["end_date"])) if raw_rule["end_date"] else None
    rule = RecurrenceRule(
        frequency=RecurrenceFrequency(str(raw_rule["frequency"])),
        interval=cast(int, raw_rule["interval"]),
        weekdays=tuple(
            cast(int, day) for day in cast(list[object], raw_rule["weekdays"])
        ),
        monthly_day=cast(int, raw_rule["monthly_day"])
        if raw_rule["monthly_day"] is not None
        else None,
        last_day=bool(raw_rule["last_day"]),
        count=cast(int, raw_rule["count"]) if raw_rule["count"] is not None else None,
        end_date=end_date,
    )
    reminders_value = value["reminders"]
    reminders = (
        tuple(_restore_offset(item) for item in cast(list[object], reminders_value))
        if isinstance(reminders_value, list)
        else None
    )
    draft = RecurringPlanDraft(
        kind=PlanKind(str(value["kind"])),
        title=str(value["title"]),
        start_date=date.fromisoformat(str(value["start_date"])),
        local_time=time.fromisoformat(str(value["local_time"]))
        if value["local_time"]
        else None,
        event_zone=str(value["event_zone"]) if value["event_zone"] else None,
        recurrence=rule,
        duration_seconds=cast(int, value["duration_seconds"])
        if value["duration_seconds"] is not None
        else None,
        end_local_time=time.fromisoformat(str(value["end_local_time"]))
        if value["end_local_time"]
        else None,
        end_local_date=date.fromisoformat(str(value["end_local_date"]))
        if value["end_local_date"]
        else None,
        fold=str(value["fold"]) if value["fold"] else None,
        end_fold=str(value["end_fold"]) if value["end_fold"] else None,
        reminders=reminders,
    )
    effective_value = cast(list[object], value["effective_reminders"])
    effective = tuple(_restore_offset(item) for item in effective_value)
    return draft, effective, str(value["default_zone"])


def _offset_payload(offset: ReminderOffset) -> dict[str, object]:
    return {"kind": offset.kind.value, "amount": offset.amount, "source": offset.source}


def _restore_offset(value: object) -> ReminderOffset:
    raw = cast(dict[str, object], value)
    return ReminderOffset(
        OffsetKind(str(raw["kind"])),
        cast(int, raw["amount"]),
        str(raw["source"]),
    )


def _schedule_payload(occurrence: RecurrenceOccurrence) -> dict[str, object]:
    schedule = occurrence.schedule
    return {
        "kind": schedule.kind.value,
        "event_zone": schedule.event_zone,
        "local_date": schedule.local_date.isoformat(),
        "local_time": schedule.local_time.isoformat() if schedule.local_time else None,
        "fold": schedule.fold,
        "starts_at": _iso(schedule.starts_at),
        "ends_at": _iso(schedule.ends_at),
        "deadline_at": _iso(schedule.deadline_at),
        "duration_seconds": schedule.duration_seconds,
        "dst_gap_shift_seconds": occurrence.dst_gap_shift_seconds,
        "repeated_time_choice": occurrence.repeated_time_choice,
    }


def _reminders_payload(occurrence: RecurrenceOccurrence) -> list[dict[str, object]]:
    return [_reminder_payload(offset, due_at) for offset, due_at in occurrence.reminders]


def _reminder_payload(offset: ReminderOffset, due_at: datetime) -> dict[str, object]:
    return {
        "kind": offset.kind.value,
        "amount": offset.amount,
        "source": offset.source,
        "due_at": _iso(due_at),
    }


def _omission_payload(omission: ReminderOmission) -> dict[str, object]:
    return {
        **_reminder_payload(omission.offset, omission.due_at),
        "reason": omission.reason,
    }


def _notice_payload(notice: ReminderNotice) -> dict[str, object]:
    return {
        "kind": notice.kind,
        "offset_kind": notice.offset.kind.value,
        "offset_amount": notice.offset.amount,
        "source": notice.offset.source,
        "original_due_at": _iso(notice.original_due_at),
        "due_at": _iso(notice.due_at),
    }


def _series_event(
    owner_id: UUID,
    series_id: UUID,
    *,
    version: int,
    at: datetime,
    event_type: str,
    payload: dict[str, object],
) -> EventEnvelope:
    return EventEnvelope(
        id=uuid4(),
        event_type=event_type,
        schema_version=1,
        aggregate_id=series_id,
        aggregate_version=version,
        owner_id=owner_id,
        occurred_at=at.astimezone(UTC),
        payload=payload,
    )


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
