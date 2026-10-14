"""Delivery-owned external attempts for persisted Accountability intents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.accountability.api import (
    AccountabilitySend,
    CurrentOccurrenceReader,
    ReviewProfile,
    TodayUntimedReader,
    authorize_accountability_send,
    mark_delivery_intent_skipped,
    record_delivery_attempt_result,
)
from pin.contexts.billing.api import CheckAccess, Operation, SqlAlchemyAccessUsageStore
from pin.contexts.notifications.application.delivery import (
    ACCOUNTABILITY_DELIVERY_JOB_KIND,
    KnownDeliveryFailure,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.domain.delivery import DeliveryOutcomeKind
from pin.contexts.notifications.infrastructure.blocked_destinations import (
    destination_is_blocked,
)
from pin.contexts.notifications.infrastructure.models import (
    AccountabilityDeliveryAttemptRecord,
)
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import JobClaim, JobLane, complete_job, fail_job, lock_job_for_effects


class AccountabilityTransport(Protocol):
    async def send_reply(self, message: TelegramReplySend) -> str | None: ...


ProfileReader = Callable[[AsyncSession, UUID], Awaitable[ReviewProfile | None]]
CurrentOccurrenceReaderFactory = Callable[[AsyncSession], CurrentOccurrenceReader]
TodayUntimedReaderFactory = Callable[[AsyncSession], TodayUntimedReader]
MessageRenderer = Callable[
    [AsyncSession, AccountabilitySend, datetime], Awaitable[TelegramReplySend]
]


async def deliver_accountability_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    transport: AccountabilityTransport,
    *,
    profile_reader: ProfileReader,
    current_occurrences_factory: CurrentOccurrenceReaderFactory,
    today_untimed_factory: TodayUntimedReaderFactory,
    render: MessageRenderer,
    now: Callable[[], datetime],
) -> DeliveryOutcomeKind | None:
    """Fence authorization and attempt-start before sending without a DB transaction."""
    if claim.lane is not JobLane.DELIVERY or claim.kind != ACCOUNTABILITY_DELIVERY_JOB_KIND:
        raise ValueError("claim is not an Accountability delivery job")
    try:
        intent_id = UUID(str(claim.payload["intent_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Accountability delivery job has an invalid intent_id") from error

    generation = claim.payload.get("dispatch_version", 1)
    if type(generation) is not int or generation < 1:
        raise ValueError("Accountability dispatch version must be a positive integer")

    prepared: tuple[UUID, int, ReviewProfile, AccountabilitySend, TelegramReplySend] | None = None
    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            owner_id = claim.owner_id
            if owner_id is None:
                await complete_job(session, claim)
                return DeliveryOutcomeKind.SKIPPED
            prior = await session.scalar(
                select(AccountabilityDeliveryAttemptRecord)
                .where(
                    AccountabilityDeliveryAttemptRecord.intent_id == intent_id,
                    AccountabilityDeliveryAttemptRecord.owner_id == owner_id,
                    AccountabilityDeliveryAttemptRecord.dispatch_version == generation,
                )
                .order_by(AccountabilityDeliveryAttemptRecord.attempt_number.desc())
                .limit(1)
                .with_for_update()
            )
            if prior is not None and prior.outcome == "started":
                prior.outcome = DeliveryOutcomeKind.UNKNOWN.value
                prior.failure_code = "worker_lost_after_send_started"
                prior.finished_at = now().astimezone(UTC)
                await record_delivery_attempt_result(
                    session,
                    intent_id=intent_id,
                    owner_id=owner_id,
                    outcome=DeliveryOutcomeKind.UNKNOWN.value,
                    retrying=False,
                    failure_code=prior.failure_code,
                    expected_dispatch_version=generation,
                )
                await _publish_outcome(
                    session,
                    owner_id=owner_id,
                    intent_id=intent_id,
                    attempt_id=prior.id,
                    attempt_number=prior.attempt_number,
                    outcome=DeliveryOutcomeKind.UNKNOWN,
                    occurred_at=prior.finished_at,
                    retrying=False,
                    failure_code=prior.failure_code,
                    target_id=prior.target_id,
                    access_period_id=prior.access_period_id,
                    dispatch_version=generation,
                )
                await complete_job(session, claim)
                return DeliveryOutcomeKind.UNKNOWN
            if prior is not None and prior.outcome in {
                DeliveryOutcomeKind.CONFIRMED.value,
                DeliveryOutcomeKind.UNKNOWN.value,
                DeliveryOutcomeKind.BLOCKED.value,
            }:
                if prior.outcome == DeliveryOutcomeKind.BLOCKED.value:
                    await mark_delivery_intent_skipped(
                        session,
                        intent_id=intent_id,
                        owner_id=owner_id,
                        reason="destination_blocked",
                        expected_dispatch_version=generation,
                    )
                await complete_job(session, claim)
                return DeliveryOutcomeKind(prior.outcome)
            access_store = SqlAlchemyAccessUsageStore(session)
            access = await access_store.lock_access(
                CheckAccess(owner_id, Operation.REMINDER_DELIVERY, now())
            )
            if not access.allowed:
                await mark_delivery_intent_skipped(
                    session,
                    intent_id=intent_id,
                    owner_id=owner_id,
                    reason=access.denial.value if access.denial is not None else "access_denied",
                    expected_dispatch_version=generation,
                )
                await complete_job(session, claim)
                return DeliveryOutcomeKind.SKIPPED
            profile = await profile_reader(session, owner_id)
            if profile is None or profile.owner_id != owner_id:
                await mark_delivery_intent_skipped(
                    session,
                    intent_id=intent_id,
                    owner_id=owner_id,
                    reason="profile_unavailable",
                    expected_dispatch_version=generation,
                )
                await complete_job(session, claim)
                return DeliveryOutcomeKind.SKIPPED
            if await destination_is_blocked(session, owner_id, profile.destination_id):
                await mark_delivery_intent_skipped(
                    session,
                    intent_id=intent_id,
                    owner_id=owner_id,
                    reason="destination_previously_blocked",
                    expected_dispatch_version=generation,
                )
                await complete_job(session, claim)
                return DeliveryOutcomeKind.BLOCKED
            authorization = await authorize_accountability_send(
                session,
                intent_id=intent_id,
                profile=profile,
                current_occurrences=current_occurrences_factory(session),
                today_untimed=today_untimed_factory(session),
                now=now(),
                expected_dispatch_version=generation,
            )
            if authorization.send is None:
                if authorization.deferred_until is not None:
                    delay = max(
                        timedelta(seconds=1), authorization.deferred_until - now().astimezone(UTC)
                    )
                    await fail_job(
                        session, claim, error=authorization.reason or "deferred", retry_after=delay
                    )
                else:
                    await complete_job(session, claim)
                return (
                    DeliveryOutcomeKind.DEFERRED
                    if authorization.deferred_until
                    else DeliveryOutcomeKind.SKIPPED
                )
            message = await render(session, authorization.send, now())
            attempt_number = (
                int(
                    await session.scalar(
                        select(func.max(AccountabilityDeliveryAttemptRecord.attempt_number)).where(
                            AccountabilityDeliveryAttemptRecord.intent_id == intent_id
                        )
                    )
                    or 0
                )
                + 1
            )
            attempt = AccountabilityDeliveryAttemptRecord(
                id=uuid4(),
                owner_id=owner_id,
                intent_id=intent_id,
                target_id=profile.destination_id,
                attempt_number=attempt_number,
                dispatch_version=generation,
                outcome="started",
                access_period_id=access.period_id,
                started_at=now().astimezone(UTC),
            )
            session.add(attempt)
            await session.flush()
            prepared = (attempt.id, attempt_number, profile, authorization.send, message)

    if prepared is None:
        return None
    attempt_id, attempt_number, profile, send, message = prepared
    outcome = DeliveryOutcomeKind.CONFIRMED
    failure_code: str | None = None
    retry_after: timedelta | None = None
    provider_message_id: str | None = None
    try:
        provider_message_id = await transport.send_reply(message)
    except KnownDeliveryFailure as error:
        failure_code = error.code
        retry_after = error.retry_after
        outcome = DeliveryOutcomeKind.BLOCKED if error.blocked else DeliveryOutcomeKind.FAILED
    except UnknownDeliveryFailure as error:
        failure_code = error.code
        outcome = DeliveryOutcomeKind.UNKNOWN
    except Exception as error:
        failure_code = f"transport_error:{type(error).__name__}"[:80]
        outcome = DeliveryOutcomeKind.UNKNOWN

    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            stored_attempt = await session.scalar(
                select(AccountabilityDeliveryAttemptRecord)
                .where(AccountabilityDeliveryAttemptRecord.id == attempt_id)
                .with_for_update()
            )
            if stored_attempt is None or stored_attempt.outcome != "started":
                raise RuntimeError("Accountability send-start receipt is missing or finalized")
            stored_attempt.outcome = outcome.value
            stored_attempt.failure_code = failure_code
            stored_attempt.provider_message_id = provider_message_id
            stored_attempt.access_period_id = access.period_id
            stored_attempt.finished_at = now().astimezone(UTC)
            retrying = outcome is DeliveryOutcomeKind.FAILED and retry_after is not None
            await record_delivery_attempt_result(
                session,
                intent_id=send.intent_id,
                owner_id=send.owner_id,
                outcome=outcome.value,
                retrying=retrying,
                failure_code=failure_code,
                expected_dispatch_version=generation,
            )
            await _publish_outcome(
                session,
                owner_id=send.owner_id,
                intent_id=send.intent_id,
                attempt_id=stored_attempt.id,
                attempt_number=attempt_number,
                outcome=outcome,
                occurred_at=stored_attempt.finished_at,
                retrying=retrying,
                failure_code=failure_code,
                target_id=stored_attempt.target_id,
                access_period_id=stored_attempt.access_period_id,
                dispatch_version=generation,
            )
            if retrying and retry_after is not None:
                await fail_job(
                    session,
                    claim,
                    error=failure_code or "confirmed_delivery_failure",
                    retry_after=retry_after,
                )
            else:
                await complete_job(session, claim)
    return outcome


async def _publish_outcome(
    session: AsyncSession,
    *,
    owner_id: UUID,
    intent_id: UUID,
    attempt_id: UUID,
    attempt_number: int,
    outcome: DeliveryOutcomeKind,
    occurred_at: datetime,
    retrying: bool,
    failure_code: str | None,
    target_id: int | None,
    access_period_id: str | None,
    dispatch_version: int,
) -> None:
    await add_outbox_event(
        session,
        EventEnvelope(
            id=uuid4(),
            event_type="notifications.delivery_attempt_outcome",
            schema_version=1,
            aggregate_id=attempt_id,
            aggregate_version=1,
            owner_id=owner_id,
            occurred_at=occurred_at,
            payload={
                "intent_id": None,
                "accountability_intent_id": str(intent_id),
                "reply_event_id": None,
                "recovery_episode_key": None,
                "target_id": target_id,
                "dispatch_version": dispatch_version,
                "attempt_id": str(attempt_id),
                "attempt_number": attempt_number,
                "outcome": outcome.value,
                "failure_code": failure_code,
                "check_in_cycle_id": None,
                "access_period_id": access_period_id,
                "quiet_conflict": False,
                "retrying": retrying,
            },
        ),
    )
