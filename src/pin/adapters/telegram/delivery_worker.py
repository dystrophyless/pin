"""Compose the existing delivery lane without holding transactions during sends."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.access_notices import prepare_access_notice, schedule_access_notices
from pin.adapters.telegram.access_resume import consume_access_activations
from pin.adapters.telegram.accountability_composition import (
    PlanningAccountabilityReads,
    read_review_profile,
    render_accountability_message,
    schedule_accountability_iteration,
)
from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.contexts.accountability.api import ReviewProfile
from pin.contexts.billing.api import SqlAlchemyStarsPaymentStore
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.api import (
    ACCOUNTABILITY_DELIVERY_JOB_KIND,
    deliver_accountability_job,
)
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.infrastructure.delivery_worker import (
    DELIVERY_JOB_KIND,
    DELIVERY_LEASE,
    DELIVERY_RECOVERY_SUMMARY_JOB_KIND,
    DELIVERY_REPLY_JOB_KIND,
    ReminderTransport,
    deliver_recovery_summary_job,
    deliver_reminder_job,
    deliver_reply_job,
    enqueue_due_reminder_jobs,
    enqueue_due_reply_jobs,
)
from pin.platform.jobs import JobLane, claim_job, fail_job

logger = logging.getLogger("pin.telegram.delivery_worker")
POLL_SECONDS = 0.5


async def process_delivery_iteration(
    sessions: async_sessionmaker[AsyncSession],
    transport: ReminderTransport,
    *,
    worker_id: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    async with sessions() as session, session.begin():
        at = now()
        await consume_access_activations(session, at=at)
        await schedule_access_notices(session, transport, at=at)
        await schedule_accountability_iteration(session, transport, at=at)
        await enqueue_due_reminder_jobs(session, now=at)
        await enqueue_due_reply_jobs(session, now=at)
        payments = SqlAlchemyStarsPaymentStore(session)
        await payments.recover_uncertain_provider_calls(at=at)
        cancellation = await payments.claim_cancellation(at=at)
        invoice = None if cancellation is not None else await payments.claim_invoice(at=at)
        invoice_language = "en"
        if invoice is not None:
            profile = await SqlAlchemyIdentityStore(session).get_preferences(invoice.owner_id)
            if profile is not None:
                invoice_language = profile.language
        claim = None
        if cancellation is None and invoice is None:
            claim = await claim_job(
                session,
                lane=JobLane.DELIVERY,
                worker_id=worker_id,
                lease_for=DELIVERY_LEASE,
            )
    payment_transport = cast(TelegramReminderTransport, transport)
    if cancellation is not None:
        try:
            confirmed = await payment_transport.cancel_stars_renewal(
                user_id=cancellation.telegram_user_id,
                first_charge_id=cancellation.first_charge_id,
            )
            outcome = "confirmed" if confirmed else "failed"
            error_code = None
        except KnownDeliveryFailure as error:
            outcome, error_code = "failed", error.code
        except UnknownDeliveryFailure as error:
            outcome, error_code = "unknown", error.code
        async with sessions() as session, session.begin():
            await SqlAlchemyStarsPaymentStore(session).finish_cancellation(
                order_id=cancellation.order_id,
                status=outcome,
                at=now(),
                error_code=error_code,
            )
        return True
    if invoice is not None:
        try:
            message_id = await payment_transport.send_stars_invoice(
                chat_id=invoice.telegram_user_id,
                payload=invoice.payload,
                language=invoice_language,
            )
            outcome, error_code = "sent", None
        except KnownDeliveryFailure as error:
            outcome, error_code, message_id = "failed", error.code, None
        except UnknownDeliveryFailure as error:
            outcome, error_code, message_id = "unknown", error.code, None
        async with sessions() as session, session.begin():
            await SqlAlchemyStarsPaymentStore(session).finish_invoice(
                order_id=invoice.order_id,
                status=outcome,
                at=now(),
                message_id=message_id,
                error_code=error_code,
            )
        return True
    if claim is None:
        return False
    if claim.kind not in {
        DELIVERY_JOB_KIND,
        DELIVERY_REPLY_JOB_KIND,
        DELIVERY_RECOVERY_SUMMARY_JOB_KIND,
        ACCOUNTABILITY_DELIVERY_JOB_KIND,
    }:
        async with sessions() as session, session.begin():
            await fail_job(session, claim, error="unsupported_delivery_kind")
        return True
    if claim.kind == ACCOUNTABILITY_DELIVERY_JOB_KIND:

        async def profile_reader(session: AsyncSession, owner_id: UUID) -> ReviewProfile | None:
            return await read_review_profile(session, owner_id, transport)

        await deliver_accountability_job(
            sessions,
            claim,
            transport,
            profile_reader=profile_reader,
            current_occurrences_factory=PlanningAccountabilityReads,
            today_untimed_factory=PlanningAccountabilityReads,
            render=render_accountability_message,
            now=now,
        )
    elif claim.kind == DELIVERY_RECOVERY_SUMMARY_JOB_KIND:
        await deliver_recovery_summary_job(
            sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=now,
        )
    elif claim.kind == DELIVERY_REPLY_JOB_KIND:
        await deliver_reply_job(
            sessions, claim, transport, now=now, prepare_reply=prepare_access_notice
        )
    else:
        await deliver_reminder_job(
            sessions,
            claim,
            transport,
            identity_store_factory=SqlAlchemyIdentityStore,
            now=now,
        )
    return True


async def run_delivery_worker(
    sessions: async_sessionmaker[AsyncSession],
    transport: ReminderTransport,
    *,
    worker_id: str,
) -> None:
    while True:
        try:
            processed = await process_delivery_iteration(sessions, transport, worker_id=worker_id)
        except Exception as error:
            # Lease recovery inspects started attempts; blindly requeuing could resend.
            logger.error("delivery_iteration_failed: error=%s", type(error).__name__)
            processed = False
        if not processed:
            await asyncio.sleep(POLL_SECONDS)
