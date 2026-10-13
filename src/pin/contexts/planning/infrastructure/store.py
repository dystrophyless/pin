"""SQLAlchemy adapter for Planning commands and versioned occurrence events."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.planning.application.contracts import PlanCommandStore
from pin.contexts.planning.domain.plans import (
    Occurrence,
    OccurrenceState,
    OffsetKind,
    PlanKind,
    PlanVersionConflict,
    ProposedPlan,
    ReminderOffset,
    ResolvedSchedule,
    resolve_reminders,
)
from pin.contexts.planning.infrastructure.models import (
    OccurrenceRecord,
    PlanItemRecord,
    ReminderRuleRecord,
)
from pin.persistence.receipts import find_action_receipt, lock_action, record_action_receipt
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event


class SqlAlchemyPlanningStore(PlanCommandStore):
    """Writes feature rows, receipts, and outbox events through the caller's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_action(self, owner_id: UUID, action_key: str) -> None:
        await lock_action(self._session, owner_id, action_key)

    async def get_receipt(self, owner_id: UUID, action_key: str) -> dict[str, object] | None:
        receipt = await find_action_receipt(self._session, owner_id, action_key)
        return dict(receipt.result) if receipt is not None else None

    async def persist_confirmed_batch(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        plans: tuple[ProposedPlan, ...],
        at: datetime,
    ) -> dict[str, object]:
        committed: list[dict[str, str]] = []
        for plan in plans:
            item = plan.item
            occurrence = plan.occurrence
            schedule = occurrence.schedule
            self._session.add(
                PlanItemRecord(
                    id=item.id,
                    owner_id=owner_id,
                    kind=item.kind.value,
                    title=item.title,
                    version=item.version,
                )
            )
            await self._session.flush()
            self._session.add(_occurrence_record(occurrence))
            for reminder in plan.reminders:
                self._session.add(
                    ReminderRuleRecord(
                        id=uuid4(),
                        owner_id=owner_id,
                        occurrence_id=occurrence.id,
                        offset_kind=reminder.offset.kind.value,
                        offset_amount=reminder.offset.amount,
                        source=reminder.offset.source,
                        due_at=reminder.due_at,
                    )
                )
            await add_outbox_event(
                self._session,
                EventEnvelope(
                    id=uuid4(),
                    event_type="planning.plan_created",
                    schema_version=1,
                    aggregate_id=occurrence.id,
                    aggregate_version=occurrence.version,
                    owner_id=owner_id,
                    occurred_at=at,
                    payload={
                        "plan_item_id": str(item.id),
                        "kind": item.kind.value,
                        "event_zone": schedule.event_zone,
                        "local_date": schedule.local_date.isoformat(),
                        "local_time": schedule.local_time.isoformat()
                        if schedule.local_time
                        else None,
                        "starts_at": _iso(schedule.starts_at),
                        "ends_at": _iso(schedule.ends_at),
                        "deadline_at": _iso(schedule.deadline_at),
                        "reminders": [
                            {
                                "offset_kind": rule.offset.kind.value,
                                "offset_amount": rule.offset.amount,
                                "source": rule.offset.source,
                                "due_at": rule.due_at.isoformat(),
                            }
                            for rule in plan.reminders
                        ],
                    },
                ),
            )
            committed.append({"plan_item_id": str(item.id), "occurrence_id": str(occurrence.id)})
        result: dict[str, object] = {
            "payload_sha256": payload_sha256,
            "plans": committed,
            "committed_at": at.isoformat(),
        }
        await record_action_receipt(self._session, owner_id, action_key, result)
        await self._session.flush()
        return result

    async def _reschedule_one_off_occurrence(
        self,
        *,
        owner_id: UUID,
        occurrence_id: UUID,
        expected_version: int,
        schedule: ResolvedSchedule,
        at: datetime,
    ) -> Occurrence:
        """Bounded mutation primitive; callers still require their own confirmed command."""
        record = await self._session.scalar(
            select(OccurrenceRecord)
            .where(
                OccurrenceRecord.owner_id == owner_id,
                OccurrenceRecord.id == occurrence_id,
            )
            .with_for_update()
        )
        if record is None or record.version != expected_version:
            raise PlanVersionConflict("occurrence is missing or changed after preview")
        current = Occurrence(
            record.id,
            record.owner_id,
            record.plan_item_id,
            _resolved_schedule(record),
            record.version,
            OccurrenceState(record.state),
        )
        updated = current.reschedule(schedule, expected_version)
        reminders = list(
            (
                await self._session.scalars(
                    select(ReminderRuleRecord).where(
                        ReminderRuleRecord.owner_id == owner_id,
                        ReminderRuleRecord.occurrence_id == occurrence_id,
                    )
                )
            ).all()
        )
        offsets = tuple(
            ReminderOffset(OffsetKind(row.offset_kind), row.offset_amount, row.source)
            for row in reminders
        )
        computed = resolve_reminders(schedule, offsets, now=at)
        due_by_offset = {
            (rule.offset.kind.value, rule.offset.amount): rule.due_at for rule in computed
        }
        for row in reminders:
            key = (row.offset_kind, row.offset_amount)
            due_at = due_by_offset.get(key)
            if due_at is None:
                await self._session.delete(row)
            else:
                row.due_at = due_at
        _apply_schedule(record, updated)
        await add_outbox_event(
            self._session,
            EventEnvelope(
                id=uuid4(),
                event_type="planning.plan_rescheduled",
                schema_version=1,
                aggregate_id=occurrence_id,
                aggregate_version=updated.version,
                owner_id=owner_id,
                occurred_at=at.astimezone(UTC),
                payload={
                    "plan_item_id": str(updated.plan_item_id),
                    "event_zone": schedule.event_zone,
                    "local_date": schedule.local_date.isoformat(),
                    "local_time": schedule.local_time.isoformat() if schedule.local_time else None,
                    "starts_at": _iso(schedule.starts_at),
                    "ends_at": _iso(schedule.ends_at),
                    "deadline_at": _iso(schedule.deadline_at),
                },
            ),
        )
        await self._session.flush()
        return updated


def _occurrence_record(occurrence: Occurrence) -> OccurrenceRecord:
    schedule = occurrence.schedule
    return OccurrenceRecord(
        id=occurrence.id,
        owner_id=occurrence.owner_id,
        plan_item_id=occurrence.plan_item_id,
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
        state=occurrence.state.value,
        version=occurrence.version,
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


def _apply_schedule(record: OccurrenceRecord, occurrence: Occurrence) -> None:
    schedule = occurrence.schedule
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
    record.version = occurrence.version


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
