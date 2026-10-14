"""Caller-UoW reactivation for reminders skipped only because access expired."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.notifications.application.contracts import (
    AccessResumeStore,
    ResumeAfterAccess,
    ResumeAfterAccessResult,
)
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)


class SqlAlchemyAccessResumeStore(AccessResumeStore):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resume_after_access(self, command: ResumeAfterAccess) -> ResumeAfterAccessResult:
        at = command.at.astimezone(UTC)
        if not command.period_id.strip() or len(command.period_id) > 128:
            raise ValueError("period_id must contain 1 to 128 characters")
        occurrences = {item.occurrence_id: item for item in command.occurrences}
        if any(item.owner_id != command.owner_id for item in command.occurrences):
            raise ValueError("resume command contains a foreign occurrence")
        if not occurrences:
            return ResumeAfterAccessResult()

        eligible_schedule_keys = {
            (
                item.occurrence_id,
                reminder.offset_kind,
                reminder.offset_amount,
                reminder.source,
                reminder.due_at.astimezone(UTC),
            )
            for item in command.occurrences
            for reminder in item.reminders
            if reminder.due_at.astimezone(UTC) <= at
            and item.event_start_at is not None
            and item.event_start_at.astimezone(UTC) > at
        }
        if not eligible_schedule_keys:
            return ResumeAfterAccessResult()

        intent_ids = [key[0] for key in eligible_schedule_keys]
        skipped_for_expiry = exists(
            select(1).where(
                DeliveryAttemptRecord.intent_id == ReminderIntentRecord.id,
                DeliveryAttemptRecord.owner_id == command.owner_id,
                DeliveryAttemptRecord.outcome == "skipped",
                DeliveryAttemptRecord.failure_code == "access_expired",
            )
        )
        externally_started_or_terminal = exists(
            select(1).where(
                DeliveryAttemptRecord.intent_id == ReminderIntentRecord.id,
                DeliveryAttemptRecord.owner_id == command.owner_id,
                DeliveryAttemptRecord.outcome.in_({"started", "confirmed", "unknown", "blocked"}),
            )
        )
        intents = list(
            (
                await self._session.scalars(
                    select(ReminderIntentRecord)
                    .where(
                        ReminderIntentRecord.owner_id == command.owner_id,
                        ReminderIntentRecord.occurrence_id.in_(set(intent_ids)),
                        ReminderIntentRecord.state == "scheduled",
                        ReminderIntentRecord.due_at <= at,
                        skipped_for_expiry,
                        ~externally_started_or_terminal,
                    )
                    .order_by(ReminderIntentRecord.due_at, ReminderIntentRecord.id)
                    .limit(1000)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        rearmed = 0
        for intent in intents:
            occurrence = occurrences.get(intent.occurrence_id)
            if occurrence is None:
                continue
            if (
                intent.plan_item_id != occurrence.plan_item_id
                or intent.plan_version != occurrence.plan_version
                or intent.occurrence_version != occurrence.occurrence_version
                or intent.preferences_version != occurrence.preferences_version
                or intent.series_id != occurrence.series_id
                or intent.series_version != occurrence.series_version
            ):
                continue
            key = (
                intent.occurrence_id,
                intent.offset_kind,
                intent.offset_amount,
                intent.source,
                intent.due_at.astimezone(UTC),
            )
            if key not in eligible_schedule_keys:
                continue
            intent.dispatch_version += 1
            intent.not_before_at = None
            rearmed += 1
        await self._session.flush()
        return ResumeAfterAccessResult(rearmed=rearmed)
