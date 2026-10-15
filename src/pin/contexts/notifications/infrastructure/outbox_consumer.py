"""Bounded local consumer for committed Planning schedule outbox events."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.api import AccountLifecycle, SqlAlchemyAccessUsageStore
from pin.contexts.notifications.application.contracts import (
    SCHEDULE_EVENT_TYPES,
    CurrentScheduleStore,
    ScheduleChange,
)
from pin.contexts.notifications.application.reconcile import reconcile_schedule_change
from pin.contexts.notifications.infrastructure.models import (
    NotificationReconciliationReceiptRecord,
)
from pin.contexts.notifications.infrastructure.store import SqlAlchemyNotificationsStore
from pin.platform.models import OutboxEvent


class SqlAlchemyNotificationScheduleEventConsumer:
    """Reconcile a bounded batch without claiming the outbox's transport handoff."""

    def __init__(self, session: AsyncSession, current_schedules: CurrentScheduleStore) -> None:
        self._session = session
        self._current_schedules = current_schedules

    async def consume_pending(self, *, at: datetime, limit: int = 100) -> int:
        """Apply unreceipted Planning schedule events in the caller's transaction.

        This is a separate in-process consumer: it deliberately ignores
        ``published_at`` and never marks an event published, which remains the
        transport publisher's responsibility.
        """
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

        receipt_exists = exists(
            select(1).where(
                NotificationReconciliationReceiptRecord.owner_id == OutboxEvent.owner_id,
                NotificationReconciliationReceiptRecord.event_id == OutboxEvent.id,
            )
        )
        candidates = tuple(
            (
                await self._session.execute(
                    select(OutboxEvent.id, OutboxEvent.owner_id)
                    .where(
                        OutboxEvent.event_type.in_(SCHEDULE_EVENT_TYPES),
                        ~receipt_exists,
                    )
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                    .limit(limit)
                )
            ).all()
        )
        intents = SqlAlchemyNotificationsStore(self._session)
        consumed = 0
        lifecycle_by_owner: dict[UUID, AccountLifecycle] = {}
        for owner_id in sorted(
            {owner_id for _, owner_id in candidates if owner_id is not None},
            key=lambda value: value.int,
        ):
            lifecycle_by_owner[owner_id] = (
                await SqlAlchemyAccessUsageStore(self._session).lock_account_lifecycle(owner_id)
            )
        for event_id, owner_id in candidates:
            if owner_id is None:
                continue
            lifecycle = lifecycle_by_owner[owner_id]
            if lifecycle is AccountLifecycle.DELETED:
                continue
            row = await self._session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            if (
                await self._session.scalar(
                    select(NotificationReconciliationReceiptRecord.id).where(
                        NotificationReconciliationReceiptRecord.owner_id == owner_id,
                        NotificationReconciliationReceiptRecord.event_id == event_id,
                    )
                )
                is not None
            ):
                continue
            change = ScheduleChange(
                event_id=row.id,
                event_type=row.event_type,
                aggregate_id=row.aggregate_id,
                aggregate_version=row.aggregate_version,
                owner_id=row.owner_id,
            )
            await reconcile_schedule_change(
                change,
                current_schedules=self._current_schedules,
                intents=intents,
                at=at,
            )
            await self._session.flush()
            consumed += 1
        return consumed
