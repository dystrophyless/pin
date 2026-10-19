"""Compose current Billing notice authorization and Telegram presentation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.presentation.subscription import render_access_expiry_notice
from pin.contexts.billing.api import (
    AccessNoticeStatus,
    AccountLifecycle,
    CheckAccessNotice,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import ReplyPreparation, TelegramReplySend
from pin.contexts.notifications.infrastructure.delivery_worker import (
    ReminderTransport,
    _quiet_end_at,
)
from pin.platform.contracts import EventEnvelope


async def prepare_access_notice(
    session: AsyncSession,
    event: EventEnvelope,
    message: TelegramReplySend,
    at: datetime,
) -> ReplyPreparation:
    marker = event.payload.get("billing_access_notice")
    if marker is None:
        if event.payload.get("requires_preparation"):
            return ReplyPreparation(reason="unknown_reply_preparation")
        return ReplyPreparation(message=message)
    if not isinstance(marker, dict) or event.owner_id is None:
        return ReplyPreparation(reason="invalid_access_notice")
    try:
        notice_id = UUID(str(marker["notice_id"]))
        period_id = marker["period_id"]
        if not isinstance(period_id, str) or not period_id:
            raise ValueError("invalid period")
    except (KeyError, ValueError, TypeError):
        return ReplyPreparation(reason="invalid_access_notice")

    class QuietPolicy:
        async def quiet_until(self, owner_id: UUID, clock: datetime) -> datetime | None:
            preferences = await SqlAlchemyIdentityStore(session).lock_preferences(owner_id)
            if preferences is None:
                return None
            return _quiet_end_at(
                clock, preferences.home_zone, preferences.quiet_start, preferences.quiet_end
            )

    decision = await SqlAlchemyAccessUsageStore(session).authorize_access_notice(
        CheckAccessNotice(event.owner_id, notice_id, period_id, at),
        quiet_policy=QuietPolicy(),
    )
    if decision.status is AccessNoticeStatus.DEFER:
        return ReplyPreparation(deferred_until=decision.defer_until, reason="quiet_hours")
    if decision.status is not AccessNoticeStatus.SEND or decision.period is None:
        return ReplyPreparation(reason="access_notice_obsolete")
    preferences = await SqlAlchemyIdentityStore(session).lock_preferences(event.owner_id)
    if preferences is None:
        return ReplyPreparation(reason="profile_unavailable")
    reply = render_access_expiry_notice(
        period_kind=decision.period.policy.kind,
        ends_at=decision.period.ends_at,
        language=preferences.language,
        zone_name=preferences.home_zone,
    )
    return ReplyPreparation(
        message=TelegramReplySend(
            destination_id=message.destination_id,
            markdown=reply.markdown,
            plain_text=reply.plain_text,
        )
    )


async def schedule_access_notices(
    session: AsyncSession, transport: ReminderTransport, *, at: datetime
) -> int:
    """Materialize bounded due notices with atomic consumer receipts and replies."""
    from uuid import uuid4

    from sqlalchemy import exists, select

    from pin.platform.events import add_outbox_event, consume_event
    from pin.platform.models import OutboxConsumption, OutboxEvent

    await SqlAlchemyAccessUsageStore(session).enqueue_due_access_notices(at=at, limit=100)
    await session.flush()
    receipt = exists(
        select(1).where(
            OutboxConsumption.consumer_name == "telegram.access_notice",
            OutboxConsumption.event_id == OutboxEvent.id,
        )
    )
    candidates = list(
        (
            await session.execute(
                select(OutboxEvent.id, OutboxEvent.owner_id)
                .where(
                    OutboxEvent.event_type == "billing.access_notice.due",
                    ~receipt,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(100)
            )
        ).all()
    )
    lifecycle_by_owner = {}
    for owner_id in sorted(
        {owner for _, owner in candidates if owner is not None}, key=lambda value: value.int
    ):
        lifecycle_by_owner[owner_id] = (
            await SqlAlchemyAccessUsageStore(session).lock_account_lifecycle(owner_id)
        )

    events = []
    for event_id, owner_id in candidates:
        if owner_id is None or lifecycle_by_owner.get(owner_id) is not AccountLifecycle.PRESENT:
            continue
        row = await session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .with_for_update(skip_locked=True)
        )
        if row is not None:
            events.append(row)

    async def materialize(db: AsyncSession, event: EventEnvelope) -> None:
        if event.owner_id is None or event.schema_version != 1:
            raise ValueError("invalid access notice envelope")
        notice_id = UUID(str(event.payload["notice_id"]))
        period_id = event.payload["period_id"]
        if not isinstance(period_id, str) or not period_id:
            raise ValueError("invalid access notice period")
        destination = await transport.resolve_destination(db, event.owner_id)
        if destination is None:
            return
        await add_outbox_event(
            db,
            EventEnvelope(
                id=uuid4(),
                event_type="telegram.reply.requested",
                schema_version=1,
                aggregate_id=notice_id,
                aggregate_version=1,
                owner_id=event.owner_id,
                occurred_at=at,
                causation_id=event.id,
                payload={
                    "chat_id": destination,
                    "markdown": "/subscription",
                    "plain_text": "/subscription",
                    "requires_preparation": True,
                    "billing_access_notice": {"notice_id": str(notice_id), "period_id": period_id},
                    "processing_classification": "deterministic",
                },
            ),
        )

    for record in events:
        envelope = EventEnvelope.from_dict(record.payload)
        if envelope.id != record.id or envelope.owner_id != record.owner_id:
            raise ValueError("access notice envelope mismatch")
        await consume_event(
            session, consumer_name="telegram.access_notice", event=envelope, handler=materialize
        )
    await session.flush()
    return len(events)
