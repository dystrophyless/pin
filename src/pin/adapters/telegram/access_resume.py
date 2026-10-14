"""Translate current Planning schedules into Notifications recovery contracts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.accountability_composition import PlanningAccountabilityReads
from pin.contexts.accountability.api import resume_initials_after_access
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.api import (
    AccessResumeOccurrence,
    AccessResumeReminder,
    ResumeAfterAccess,
    resume_after_access,
)
from pin.contexts.notifications.infrastructure.access_resume_store import (
    SqlAlchemyAccessResumeStore,
)
from pin.contexts.planning.api import SqlAlchemyPlanningScheduleSnapshotStore


async def resume_useful_reminders(
    session: AsyncSession,
    *,
    owner_id: UUID,
    period_id: str,
    at: datetime,
) -> int:
    reader = SqlAlchemyPlanningScheduleSnapshotStore(session, SqlAlchemyIdentityStore(session))
    store = SqlAlchemyAccessResumeStore(session)
    accountability_reads = PlanningAccountabilityReads(session)
    cursor: UUID | None = None
    total = 0
    while True:
        page = await reader.list_current_owner_schedules(
            owner_id,
            after_occurrence_id=cursor,
            limit=100,
        )
        snapshots = tuple(
            AccessResumeOccurrence(
                owner_id=item.owner_id,
                occurrence_id=item.occurrence.occurrence_id,
                occurrence_version=item.occurrence.occurrence_version,
                state=item.occurrence.state,
                event_start_at=item.occurrence.schedule.anchor_at,
                reminders=tuple(
                    AccessResumeReminder(
                        offset_kind=reminder.offset.kind.value,
                        offset_amount=reminder.offset.amount,
                        source=reminder.offset.source,
                        due_at=reminder.due_at,
                    )
                    for reminder in item.occurrence.reminders
                ),
                plan_item_id=item.plan_item_id,
                plan_version=item.plan_version,
                preferences_version=item.preferences_version,
                series_id=item.occurrence.series_id,
                series_version=item.occurrence.series_version,
            )
            for item in page.items
        )
        result = await resume_after_access(
            ResumeAfterAccess(owner_id, period_id, at, snapshots),
            store=store,
        )
        total += result.rearmed
        current_occurrences = []
        for item in page.items:
            current = await accountability_reads.get_current_occurrence(
                owner_id,
                item.occurrence.occurrence_id,
                for_update=True,
            )
            if current is not None:
                current_occurrences.append(current)
        await resume_initials_after_access(
            session,
            owner_id=owner_id,
            period_id=period_id,
            current_occurrences=tuple(current_occurrences),
            now=at,
        )
        if page.next_cursor is None:
            return total
        if page.next_cursor == cursor:
            raise RuntimeError("Planning resume cursor did not advance")
        cursor = page.next_cursor


async def consume_access_activations(session: AsyncSession, *, at: datetime) -> int:
    """Recover one current period once, atomically with the activation receipt."""
    from sqlalchemy import exists, select

    from pin.contexts.billing.api import CheckAccess, Operation, SqlAlchemyAccessUsageStore
    from pin.contexts.notifications.application.delivery import RecoverySummaryRequest
    from pin.contexts.notifications.infrastructure.delivery_worker import (
        enqueue_recovery_summary_job,
    )
    from pin.platform.contracts import EventEnvelope
    from pin.platform.events import consume_event
    from pin.platform.models import OutboxConsumption, OutboxEvent

    receipt = exists(
        select(1).where(
            OutboxConsumption.consumer_name == "telegram.access_resume",
            OutboxConsumption.event_id == OutboxEvent.id,
        )
    )
    events = list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "billing.access_period.activated",
                    ~receipt,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )

    async def recover(db: AsyncSession, event: EventEnvelope) -> None:
        period_id = event.payload.get("period_id")
        if event.owner_id is None or not isinstance(period_id, str) or not period_id:
            raise ValueError("invalid access activation payload")
        access = await SqlAlchemyAccessUsageStore(db).lock_access(
            CheckAccess(event.owner_id, Operation.REMINDER_DELIVERY, at),
        )
        if not access.allowed or access.period_id != period_id:
            return
        await resume_useful_reminders(db, owner_id=event.owner_id, period_id=period_id, at=at)
        await enqueue_recovery_summary_job(db, RecoverySummaryRequest(event.owner_id, period_id))

    for row in events:
        event = EventEnvelope.from_dict(row.payload)
        if (
            event.id != row.id
            or event.owner_id != row.owner_id
            or event.event_type != row.event_type
            or event.schema_version != 1
        ):
            raise ValueError("access activation envelope mismatch")
        await consume_event(
            session, consumer_name="telegram.access_resume", event=event, handler=recover
        )
    await session.flush()
    return len(events)
