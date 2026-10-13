"""Caller-owned PostgreSQL reconciliation adapter for reminder intents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.notifications.application.contracts import (
    CurrentOccurrenceSchedule,
    CurrentPlanSchedule,
    ReconciliationResult,
    ReminderIntentStore,
    ScheduleChange,
)
from pin.contexts.notifications.domain.reminders import ReminderCandidate
from pin.contexts.notifications.infrastructure.models import (
    NotificationReconciliationReceiptRecord,
    ReminderIntentRecord,
)


class SqlAlchemyNotificationsStore(ReminderIntentStore):
    """Persist current schedule intentions and receipts in the caller's UoW."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reconcile_snapshot(
        self,
        change: ScheduleChange,
        current: CurrentPlanSchedule,
        *,
        at: datetime,
    ) -> ReconciliationResult:
        _require_aware(at)
        existing_receipt = await self._receipt(change.owner_id, change.event_id)
        if existing_receipt is not None:
            return _receipt_result(existing_receipt, duplicate=True)

        desired: dict[
            tuple[UUID, str, int], tuple[CurrentOccurrenceSchedule, ReminderCandidate]
        ] = {}
        for occurrence in current.occurrences:
            # A preference edit leaves prior confirmed reminders unqualified until
            # Planning confirms a schedule under the current version.
            if (
                occurrence.state != "open"
                or occurrence.approved_preferences_version != current.preferences_version
            ):
                continue
            for reminder in occurrence.reminders:
                key = (occurrence.occurrence_id, reminder.offset_kind, reminder.offset_amount)
                if key in desired:
                    raise ValueError("current schedule contains duplicate reminder intents")
                desired[key] = (occurrence, reminder)

        current_rows = list(
            (
                await self._session.scalars(
                    select(ReminderIntentRecord)
                    .where(
                        ReminderIntentRecord.owner_id == change.owner_id,
                        ReminderIntentRecord.plan_item_id == current.plan_item_id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        by_key = {
            (row.occurrence_id, row.offset_kind, row.offset_amount): row for row in current_rows
        }
        created = updated = invalidated = 0
        for key, (occurrence, reminder) in desired.items():
            source = reminder.source
            due_at = reminder.due_at.astimezone(UTC)
            existing = by_key.get(key)
            if existing is None:
                self._session.add(
                    ReminderIntentRecord(
                        id=uuid4(),
                        owner_id=change.owner_id,
                        plan_item_id=current.plan_item_id,
                        occurrence_id=key[0],
                        series_id=occurrence.series_id,
                        plan_version=current.plan_version,
                        occurrence_version=occurrence.occurrence_version,
                        preferences_version=occurrence.approved_preferences_version,
                        series_version=occurrence.series_version,
                        offset_kind=key[1],
                        offset_amount=key[2],
                        source=source,
                        due_at=due_at,
                        state="scheduled",
                        dispatch_version=1,
                        source_event_id=change.event_id,
                    )
                )
                created += 1
                continue

            changed = (
                existing.state != "scheduled"
                or existing.due_at != due_at
                or existing.source != source
                or existing.plan_version != current.plan_version
                or existing.occurrence_version != occurrence.occurrence_version
                or existing.preferences_version != occurrence.approved_preferences_version
                or existing.series_id != occurrence.series_id
                or existing.series_version != occurrence.series_version
            )
            if changed:
                existing.state = "scheduled"
                existing.due_at = due_at
                existing.source = source
                existing.plan_version = current.plan_version
                existing.occurrence_version = occurrence.occurrence_version
                existing.preferences_version = cast(int, occurrence.approved_preferences_version)
                existing.series_id = occurrence.series_id
                existing.series_version = occurrence.series_version
                existing.source_event_id = change.event_id
                existing.dispatch_version += 1
                existing.invalidated_at = None
                updated += 1

        desired_keys = set(desired)
        for key, row in by_key.items():
            if row.state == "scheduled" and key not in desired_keys:
                row.state = "invalidated"
                row.invalidated_at = at.astimezone(UTC)
                row.dispatch_version += 1
                invalidated += 1

        result = ReconciliationResult("applied", created, updated, invalidated)
        await self._record_receipt(change, result)
        await self._session.flush()
        return result

    async def record_stale_change(
        self, change: ScheduleChange, *, at: datetime
    ) -> ReconciliationResult:
        _require_aware(at)
        existing = await self._receipt(change.owner_id, change.event_id)
        if existing is not None:
            return _receipt_result(existing, duplicate=True)
        result = ReconciliationResult("stale")
        await self._record_receipt(change, result)
        await self._session.flush()
        return result

    async def _receipt(
        self, owner_id: UUID, event_id: UUID
    ) -> NotificationReconciliationReceiptRecord | None:
        return cast(
            NotificationReconciliationReceiptRecord | None,
            await self._session.scalar(
                select(NotificationReconciliationReceiptRecord).where(
                    NotificationReconciliationReceiptRecord.owner_id == owner_id,
                    NotificationReconciliationReceiptRecord.event_id == event_id,
                )
            ),
        )

    async def _record_receipt(
        self, change: ScheduleChange, result: ReconciliationResult
    ) -> NotificationReconciliationReceiptRecord:
        statement = (
            insert(NotificationReconciliationReceiptRecord)
            .values(
                owner_id=change.owner_id,
                event_id=change.event_id,
                event_type=change.event_type,
                aggregate_id=change.aggregate_id,
                aggregate_version=change.aggregate_version,
                status="stale" if result.status == "stale" else "applied",
                created_count=result.created,
                updated_count=result.updated,
                invalidated_count=result.invalidated,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    NotificationReconciliationReceiptRecord.owner_id,
                    NotificationReconciliationReceiptRecord.event_id,
                ]
            )
            .returning(NotificationReconciliationReceiptRecord)
        )
        receipt = (await self._session.execute(statement)).scalar_one_or_none()
        if receipt is not None:
            return receipt
        existing = await self._receipt(change.owner_id, change.event_id)
        if existing is None:
            raise RuntimeError("notification receipt conflict has no matching owner/event receipt")
        return existing


def _receipt_result(
    receipt: NotificationReconciliationReceiptRecord, *, duplicate: bool
) -> ReconciliationResult:
    return ReconciliationResult(
        "duplicate" if duplicate else receipt.status,
        receipt.created_count,
        receipt.updated_count,
        receipt.invalidated_count,
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reconciliation time must be timezone-aware")
