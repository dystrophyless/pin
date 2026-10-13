"""Fenced delivery-lane pump and Telegram boundary orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import String, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.contexts.billing.api import (
    AccountLifecycle,
    CheckAccess,
    Operation,
    SqlAlchemyAccessUsageStore,
)
from pin.contexts.identity.api import IdentityStore, PreferencesSnapshot
from pin.contexts.notifications.application.contracts import (
    CurrentOccurrenceSchedule,
    CurrentPlanSchedule,
    ScheduleChange,
)
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    RecoverySummaryRequest,
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.domain.delivery import (
    DeliveryAttemptOutcome,
    DeliveryOutcomeKind,
    RecoverySummaryItem,
    render_recovery_summary,
    render_reminder,
)
from pin.contexts.notifications.infrastructure.models import (
    DeliveryAttemptRecord,
    ReminderIntentRecord,
)
from pin.contexts.planning.api import (
    AgendaRecord,
    SqlAlchemyAgendaReadStore,
    SqlAlchemyReminderScheduleStore,
)
from pin.platform.contracts import EventEnvelope
from pin.platform.events import add_outbox_event
from pin.platform.jobs import (
    JobClaim,
    JobLane,
    complete_job,
    enqueue_job,
    fail_job,
    lock_job_for_effects,
)
from pin.platform.models import Job, OutboxEvent

DELIVERY_JOB_KIND = "notifications.deliver_reminder"
DELIVERY_REPLY_JOB_KIND = "notifications.deliver_reply"
DELIVERY_RECOVERY_SUMMARY_JOB_KIND = "notifications.deliver_recovery_summary"
DELIVERY_JOB_MAX_ATTEMPTS = 8
DELIVERY_LEASE = timedelta(minutes=2)
_TERMINAL_OUTCOMES = frozenset(
    {
        DeliveryOutcomeKind.CONFIRMED.value,
        DeliveryOutcomeKind.UNKNOWN.value,
        DeliveryOutcomeKind.BLOCKED.value,
        DeliveryOutcomeKind.SKIPPED.value,
        DeliveryOutcomeKind.DEFERRED.value,
        DeliveryOutcomeKind.COALESCED.value,
    }
)


class ReminderTransport(Protocol):
    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None: ...

    async def send_reminder(self, message: ReminderSend) -> str | None: ...

    async def send_reply(self, message: TelegramReplySend) -> str | None: ...


IdentityStoreFactory = Callable[[AsyncSession], IdentityStore]


@dataclass(frozen=True, slots=True)
class _PreparedSend:
    attempt_id: UUID
    intent_id: UUID
    owner_id: UUID
    dispatch_version: int
    target_id: int
    attempt_number: int
    check_in_cycle_id: UUID | None
    access_period_id: str | None
    quiet_conflict: bool
    message: ReminderSend


@dataclass(frozen=True, slots=True)
class _PreparedReply:
    attempt_id: UUID
    reply_event_id: UUID
    owner_id: UUID
    target_id: int
    attempt_number: int
    message: TelegramReplySend


@dataclass(frozen=True, slots=True)
class _PreparedRecoverySummary:
    attempt_id: UUID
    owner_id: UUID
    episode_key: str
    target_id: int
    attempt_number: int
    message: TelegramReplySend


async def enqueue_due_reminder_jobs(
    session: AsyncSession, *, now: datetime, limit: int = 100
) -> int:
    """Move a bounded set of due intents onto the existing durable delivery lane."""
    _require_aware(now)
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    due = list(
        (
            await session.scalars(
                select(ReminderIntentRecord)
                .where(
                    ReminderIntentRecord.state == "scheduled",
                    func.coalesce(
                        ReminderIntentRecord.not_before_at, ReminderIntentRecord.due_at
                    )
                    <= now.astimezone(UTC),
                    ~select(Job.id)
                    .where(
                        Job.lane == JobLane.DELIVERY.value,
                        Job.deduplication_key
                        == func.concat(
                            "reminder:",
                            sql_cast(ReminderIntentRecord.id, String),
                            ":",
                            sql_cast(ReminderIntentRecord.dispatch_version, String),
                        ),
                    )
                    .exists(),
                )
                .order_by(ReminderIntentRecord.due_at, ReminderIntentRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    count = 0
    for intent in due:
        key = _job_key(intent.id, intent.dispatch_version)
        await enqueue_job(
            session,
            lane=JobLane.DELIVERY,
            kind=DELIVERY_JOB_KIND,
            payload={
                "intent_id": str(intent.id),
                "dispatch_version": intent.dispatch_version,
            },
            deduplication_key=key,
            owner_id=intent.owner_id,
            max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
        )
        count += 1
    return count


async def enqueue_due_reply_jobs(
    session: AsyncSession, *, now: datetime, limit: int = 100
) -> int:
    """Move queued private replies onto the existing durable delivery lane."""
    _require_aware(now)
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    events = list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "telegram.reply.requested",
                    OutboxEvent.created_at <= now.astimezone(UTC),
                    ~select(DeliveryAttemptRecord.id)
                    .where(DeliveryAttemptRecord.reply_event_id == OutboxEvent.id)
                    .exists(),
                    ~select(Job.id)
                    .where(
                        Job.lane == JobLane.DELIVERY.value,
                        Job.deduplication_key == func.concat(
                            "reply:", sql_cast(OutboxEvent.id, String)
                        ),
                    )
                    .exists(),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    count = 0
    for event in events:
        envelope = _event_envelope(event)
        if envelope is None or envelope.owner_id != event.owner_id:
            continue
        payload = envelope.payload
        target = _positive_int(payload.get("chat_id"))
        if event.owner_id is None or target is None:
            continue
        await enqueue_job(
            session,
            lane=JobLane.DELIVERY,
            kind=DELIVERY_REPLY_JOB_KIND,
            payload={"reply_event_id": str(event.id)},
            deduplication_key=_reply_job_key(event.id),
            owner_id=event.owner_id,
            max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
        )
        count += 1
    return count


async def enqueue_recovery_summary_job(
    session: AsyncSession, request: RecoverySummaryRequest
) -> Job:
    """Enqueue one durable recovery summary for an owner and caller-owned episode."""
    return await enqueue_job(
        session,
        lane=JobLane.DELIVERY,
        kind=DELIVERY_RECOVERY_SUMMARY_JOB_KIND,
        payload={"owner_id": str(request.owner_id), "episode_key": request.episode_key},
        deduplication_key=_recovery_summary_job_key(request.owner_id, request.episode_key),
        owner_id=request.owner_id,
        max_attempts=DELIVERY_JOB_MAX_ATTEMPTS,
    )


async def deliver_reminder_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    transport: ReminderTransport,
    *,
    identity_store_factory: IdentityStoreFactory,
    now: Callable[[], datetime],
    before_attempt_authorization: Callable[[], Awaitable[None]] | None = None,
) -> DeliveryAttemptOutcome | None:
    """Authorize under a live lease, send with no open transaction, then fence the result."""
    if claim.lane is not JobLane.DELIVERY or claim.kind != DELIVERY_JOB_KIND:
        raise ValueError("claim is not a reminder delivery job")
    intent_id, dispatch_version, cycle_id = _job_payload(claim)
    prepared: _PreparedSend | None = None
    immediate: DeliveryAttemptOutcome | None = None
    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            intent = await session.scalar(
                select(ReminderIntentRecord)
                .where(
                    ReminderIntentRecord.id == intent_id,
                    ReminderIntentRecord.owner_id == claim.owner_id,
                )
                .with_for_update()
            )
            if intent is None:
                await complete_job(session, claim)
                return None

            destination = await transport.resolve_destination(session, intent.owner_id)
            if destination is None:
                started = await session.scalar(
                    select(DeliveryAttemptRecord)
                    .where(
                        DeliveryAttemptRecord.intent_id == intent.id,
                        DeliveryAttemptRecord.dispatch_version == dispatch_version,
                        DeliveryAttemptRecord.outcome == "started",
                    )
                    .order_by(DeliveryAttemptRecord.attempt_number.desc())
                    .limit(1)
                    .with_for_update()
                )
                if started is not None:
                    immediate = await _finish_existing_as_unknown(
                        session, claim, started, at=now()
                    )
                else:
                    immediate = await _record_outcome(
                        session,
                        intent,
                        target_id=None,
                        attempt_number=await _next_attempt_number(session, intent, None),
                        cycle_id=cycle_id,
                        outcome=DeliveryOutcomeKind.BLOCKED,
                        at=now(),
                        failure_code="destination_missing",
                    )
                    await complete_job(session, claim)
            else:
                prior = await _latest_attempt_for_version(
                    session, intent.id, dispatch_version, destination
                )
                if prior is not None and prior.outcome == "started":
                    immediate = await _finish_existing_as_unknown(
                        session, claim, prior, at=now()
                    )
                elif prior is not None and prior.outcome in _TERMINAL_OUTCOMES:
                    immediate = _outcome_from_record(intent, prior)
                    await complete_job(session, claim)
                elif (
                intent.dispatch_version != dispatch_version
                or intent.state != "scheduled"
                ):
                    immediate = await _record_outcome(
                        session,
                        intent,
                        target_id=destination,
                        attempt_number=await _next_attempt_number(session, intent, destination),
                        cycle_id=cycle_id,
                        outcome=DeliveryOutcomeKind.SKIPPED,
                        at=now(),
                        failure_code="stale_intent_version",
                    )
                    await complete_job(session, claim)
                else:
                    decision = await _current_schedule(
                        session, intent, identity_store_factory(session)
                    )
                    if decision is None:
                        immediate = await _record_outcome(
                            session,
                            intent,
                            target_id=destination,
                            attempt_number=await _next_attempt_number(session, intent, destination),
                            cycle_id=cycle_id,
                            outcome=DeliveryOutcomeKind.SKIPPED,
                            at=now(),
                            failure_code="obsolete_or_incomplete_schedule",
                        )
                        await complete_job(session, claim)
                    else:
                        preferences, current_occurrence = decision
                        access_store = SqlAlchemyAccessUsageStore(session)
                        access = await access_store.lock_access(
                            CheckAccess(intent.owner_id, Operation.REMINDER_DELIVERY, now())
                        )
                        if not access.allowed:
                            immediate = await _record_outcome(
                                session,
                                intent,
                                target_id=destination,
                                attempt_number=await _next_attempt_number(
                                    session, intent, destination
                                ),
                                cycle_id=cycle_id,
                                outcome=DeliveryOutcomeKind.SKIPPED,
                                at=now(),
                                failure_code=(
                                    access.denial.value
                                    if access.denial is not None
                                    else "access_denied"
                                ),
                                access_period_id=access.period_id,
                            )
                            await complete_job(session, claim)
                        elif await _destination_blocked(session, intent.owner_id, destination):
                            immediate = await _record_outcome(
                                session,
                                intent,
                                target_id=destination,
                                attempt_number=await _next_attempt_number(
                                    session, intent, destination
                                ),
                                cycle_id=cycle_id,
                                outcome=DeliveryOutcomeKind.BLOCKED,
                                at=now(),
                                failure_code="destination_previously_blocked",
                                access_period_id=access.period_id,
                            )
                            await complete_job(session, claim)
                        else:
                            prior = await _latest_attempt(session, intent, destination)
                            if prior is not None and prior.outcome == "started":
                                immediate = await _finish_existing_as_unknown(
                                    session, claim, prior, at=now()
                                )
                            elif prior is not None and prior.outcome in _TERMINAL_OUTCOMES:
                                immediate = _outcome_from_record(intent, prior)
                                await complete_job(session, claim)
                            elif intent.due_at > now().astimezone(UTC):
                                immediate = await _record_outcome(
                                    session,
                                    intent,
                                    target_id=destination,
                                    attempt_number=await _next_attempt_number(
                                        session, intent, destination
                                    ),
                                    cycle_id=cycle_id,
                                    outcome=DeliveryOutcomeKind.SKIPPED,
                                    at=now(),
                                    failure_code="not_due",
                                    access_period_id=access.period_id,
                                )
                                await complete_job(session, claim)
                            elif (
                                current_occurrence.event_start_at is not None
                                and current_occurrence.event_start_at <= now().astimezone(UTC)
                            ):
                                immediate = await _record_outcome(
                                    session,
                                    intent,
                                    target_id=destination,
                                    attempt_number=await _next_attempt_number(
                                        session, intent, destination
                                    ),
                                    cycle_id=cycle_id,
                                    outcome=DeliveryOutcomeKind.SKIPPED,
                                    at=now(),
                                    failure_code="event_already_started",
                                    access_period_id=access.period_id,
                                )
                                await complete_job(session, claim)
                            elif await _defer_if_quiet(
                                session,
                                claim,
                                intent,
                                destination,
                                cycle_id,
                                preferences.home_zone,
                                preferences.quiet_start,
                                preferences.quiet_end,
                                current_occurrence.event_start_at,
                                access.period_id,
                                now(),
                            ):
                                pass
                            elif await _coalesce_if_older(
                                session,
                                claim,
                                intent,
                                destination,
                                cycle_id,
                                access.period_id,
                                now(),
                            ):
                                pass
                            else:
                                title_records = await SqlAlchemyAgendaReadStore(
                                    session
                                ).list_snapshot_records(
                                    intent.owner_id, (intent.occurrence_id,)
                                )
                                title_record = next(
                                    (
                                        row
                                        for row in title_records
                                        if row.occurrence_id == intent.occurrence_id
                                        and row.owner_id == intent.owner_id
                                    ),
                                    None,
                                )
                                if title_record is None or title_record.state != "open":
                                    immediate = await _record_outcome(
                                        session,
                                        intent,
                                        target_id=destination,
                                        attempt_number=await _next_attempt_number(
                                            session, intent, destination
                                        ),
                                        cycle_id=cycle_id,
                                        outcome=DeliveryOutcomeKind.SKIPPED,
                                        at=now(),
                                        failure_code="current_plan_missing",
                                        access_period_id=access.period_id,
                                    )
                                    await complete_job(session, claim)
                                else:
                                    if before_attempt_authorization is not None:
                                        await before_attempt_authorization()
                                    await lock_job_for_effects(session, claim)
                                    authorized_at = now()
                                    latest_access = await access_store.lock_access(
                                        CheckAccess(
                                            intent.owner_id,
                                            Operation.REMINDER_DELIVERY,
                                            authorized_at,
                                        )
                                    )
                                    if not latest_access.allowed:
                                        immediate = await _record_outcome(
                                            session,
                                            intent,
                                            target_id=destination,
                                            attempt_number=await _next_attempt_number(
                                                session, intent, destination
                                            ),
                                            cycle_id=cycle_id,
                                            outcome=DeliveryOutcomeKind.SKIPPED,
                                            at=authorized_at,
                                            failure_code=(
                                                latest_access.denial.value
                                                if latest_access.denial is not None
                                                else "access_denied"
                                            ),
                                            access_period_id=latest_access.period_id,
                                        )
                                        await complete_job(session, claim)
                                    elif intent.due_at > authorized_at.astimezone(UTC):
                                        immediate = await _record_outcome(
                                            session,
                                            intent,
                                            target_id=destination,
                                            attempt_number=await _next_attempt_number(
                                                session, intent, destination
                                            ),
                                            cycle_id=cycle_id,
                                            outcome=DeliveryOutcomeKind.SKIPPED,
                                            at=authorized_at,
                                            failure_code="not_due",
                                            access_period_id=latest_access.period_id,
                                        )
                                        await complete_job(session, claim)
                                    elif (
                                        current_occurrence.event_start_at is not None
                                        and current_occurrence.event_start_at
                                        <= authorized_at.astimezone(UTC)
                                    ):
                                        immediate = await _record_outcome(
                                            session,
                                            intent,
                                            target_id=destination,
                                            attempt_number=await _next_attempt_number(
                                                session, intent, destination
                                            ),
                                            cycle_id=cycle_id,
                                            outcome=DeliveryOutcomeKind.SKIPPED,
                                            at=authorized_at,
                                            failure_code="event_already_started",
                                            access_period_id=latest_access.period_id,
                                        )
                                        await complete_job(session, claim)
                                    elif await _defer_if_quiet(
                                        session,
                                        claim,
                                        intent,
                                        destination,
                                        cycle_id,
                                        preferences.home_zone,
                                        preferences.quiet_start,
                                        preferences.quiet_end,
                                        current_occurrence.event_start_at,
                                        latest_access.period_id,
                                        authorized_at,
                                    ):
                                        pass
                                    else:
                                        attempt_number = await _next_attempt_number(
                                            session, intent, destination
                                        )
                                        attempt = DeliveryAttemptRecord(
                                            id=uuid4(),
                                            owner_id=intent.owner_id,
                                            intent_id=intent.id,
                                            target_id=destination,
                                            dispatch_version=intent.dispatch_version,
                                            attempt_number=attempt_number,
                                            check_in_cycle_id=cycle_id,
                                            access_period_id=latest_access.period_id,
                                            quiet_conflict=(
                                                intent.source == "explicit"
                                                and _quiet_end_at(
                                                    authorized_at,
                                                    preferences.home_zone,
                                                    preferences.quiet_start,
                                                    preferences.quiet_end,
                                                )
                                                is not None
                                            ),
                                            outcome="started",
                                            started_at=authorized_at.astimezone(UTC),
                                        )
                                        session.add(attempt)
                                        await session.flush()
                                        prepared = _PreparedSend(
                                            attempt.id,
                                            intent.id,
                                            intent.owner_id,
                                            intent.dispatch_version,
                                            destination,
                                            attempt_number,
                                            cycle_id,
                                            latest_access.period_id,
                                            attempt.quiet_conflict,
                                            ReminderSend(
                                                destination,
                                                render_reminder(
                                                    title_record.title, preferences.language
                                                ),
                                            ),
                                        )
    if prepared is None:
        return immediate

    outcome = DeliveryOutcomeKind.CONFIRMED
    failure_code: str | None = None
    retry_after: timedelta | None = None
    blocked = False
    provider_message_id: str | None = None
    try:
        provider_message_id = await transport.send_reminder(prepared.message)
    except KnownDeliveryFailure as error:
        failure_code = error.code
        retry_after = error.retry_after
        blocked = error.blocked
        outcome = DeliveryOutcomeKind.BLOCKED if blocked else DeliveryOutcomeKind.FAILED
    except UnknownDeliveryFailure as error:
        failure_code = error.code
        outcome = DeliveryOutcomeKind.UNKNOWN
    except Exception as error:
        failure_code = f"transport_error:{type(error).__name__}"[:80]
        outcome = DeliveryOutcomeKind.UNKNOWN

    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            attempt_record = await session.scalar(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.id == prepared.attempt_id)
                .with_for_update()
            )
            if attempt_record is None or attempt_record.outcome != "started":
                raise RuntimeError("delivery attempt start receipt is missing or already final")
            attempt_record.outcome = outcome.value
            attempt_record.failure_code = failure_code
            attempt_record.provider_message_id = provider_message_id
            attempt_record.quiet_conflict = prepared.quiet_conflict
            attempt_record.finished_at = now().astimezone(UTC)
            result = _outcome_from_record(_intent_stub(prepared), attempt_record)
            await _publish_outcome(
                session, result, attempt_number=attempt_record.attempt_number
            )
            if outcome is DeliveryOutcomeKind.FAILED and retry_after is not None:
                await fail_job(
                    session,
                    claim,
                    error=failure_code or "known_transient_failure",
                    retry_after=retry_after,
                )
            else:
                await complete_job(session, claim)
    return result


async def deliver_reply_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    transport: ReminderTransport,
    *,
    now: Callable[[], datetime],
    before_attempt_authorization: Callable[[], Awaitable[None]] | None = None,
) -> DeliveryAttemptOutcome | None:
    """Send a durable private reply without plan/access eligibility gates."""
    if claim.lane is not JobLane.DELIVERY or claim.kind != DELIVERY_REPLY_JOB_KIND:
        raise ValueError("claim is not a Telegram reply delivery job")
    try:
        reply_event_id = UUID(str(claim.payload["reply_event_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("reply delivery job has an invalid payload") from error

    prepared: _PreparedReply | None = None
    immediate: DeliveryAttemptOutcome | None = None
    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            event = await session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.id == reply_event_id, OutboxEvent.owner_id == claim.owner_id)
                .with_for_update()
            )
            if event is None or event.event_type != "telegram.reply.requested":
                await complete_job(session, claim)
                return None

            envelope = _event_envelope(event)
            if envelope is None or envelope.owner_id != event.owner_id:
                await complete_job(session, claim)
                return None
            payload = envelope.payload
            requested_target = _positive_int(payload.get("chat_id"))
            destination = await transport.resolve_destination(session, event.owner_id)
            access_store = SqlAlchemyAccessUsageStore(session)
            lifecycle = await access_store.lock_account_lifecycle(
                event.owner_id
            )
            # The persisted identity mapping is authoritative. A copied or stale
            # payload cannot redirect another owner's text to a different chat.
            if lifecycle is AccountLifecycle.DELETED:
                immediate = await _record_reply_outcome(
                    session,
                    event,
                    target_id=destination,
                    attempt_number=await _next_reply_attempt_number(
                        session, event.id, destination
                    ),
                    outcome=DeliveryOutcomeKind.SKIPPED,
                    at=now(),
                    failure_code="owner_deleted",
                )
                await complete_job(session, claim)
            elif destination is None or requested_target != destination:
                target_id = destination if destination is not None else requested_target
                immediate = await _record_reply_outcome(
                    session,
                    event,
                    target_id=target_id,
                    attempt_number=await _next_reply_attempt_number(
                        session, event.id, target_id
                    ),
                    outcome=(
                        DeliveryOutcomeKind.BLOCKED
                        if destination is None
                        else DeliveryOutcomeKind.SKIPPED
                    ),
                    at=now(),
                    failure_code=(
                        "reply_destination_unavailable"
                        if destination is None
                        else "reply_destination_mismatch"
                    ),
                )
                await complete_job(session, claim)
            else:
                prior = await _latest_reply_attempt(session, event.id, destination)
                if prior is not None and prior.outcome == "started":
                    immediate = await _finish_reply_as_unknown(
                        session, claim, event, prior, at=now()
                    )
                elif prior is not None and prior.outcome in _TERMINAL_OUTCOMES:
                    immediate = _reply_outcome_from_record(event, prior)
                    await complete_job(session, claim)
                else:
                    markdown = payload.get("markdown")
                    plain_text = payload.get("plain_text")
                    markup = payload.get("reply_markup")
                    if (
                        not isinstance(markdown, str)
                        or not markdown.strip()
                        or not isinstance(plain_text, str)
                        or not plain_text.strip()
                        or (markup is not None and not isinstance(markup, dict))
                    ):
                        immediate = await _record_reply_outcome(
                            session,
                            event,
                            target_id=destination,
                            attempt_number=await _next_reply_attempt_number(
                                session, event.id, destination
                            ),
                            outcome=DeliveryOutcomeKind.SKIPPED,
                            at=now(),
                            failure_code="reply_payload_invalid",
                        )
                        await complete_job(session, claim)
                    else:
                        if before_attempt_authorization is not None:
                            await before_attempt_authorization()
                        await lock_job_for_effects(session, claim)
                        latest_lifecycle = await access_store.lock_account_lifecycle(
                            event.owner_id
                        )
                        if latest_lifecycle is AccountLifecycle.DELETED:
                            immediate = await _record_reply_outcome(
                                session,
                                event,
                                target_id=destination,
                                attempt_number=await _next_reply_attempt_number(
                                    session, event.id, destination
                                ),
                                outcome=DeliveryOutcomeKind.SKIPPED,
                                at=now(),
                                failure_code="owner_deleted",
                            )
                            await complete_job(session, claim)
                        else:
                            attempt_number = await _next_reply_attempt_number(
                                session, event.id, destination
                            )
                            attempt = DeliveryAttemptRecord(
                                id=uuid4(),
                                owner_id=event.owner_id,
                                reply_event_id=event.id,
                                target_id=destination,
                                dispatch_version=1,
                                attempt_number=attempt_number,
                                outcome="started",
                                started_at=now().astimezone(UTC),
                            )
                            session.add(attempt)
                            await session.flush()
                            prepared = _PreparedReply(
                                attempt.id,
                                event.id,
                                event.owner_id,
                                destination,
                                attempt_number,
                                TelegramReplySend(destination, markdown, plain_text, markup),
                            )

    if prepared is None:
        return immediate

    outcome = DeliveryOutcomeKind.CONFIRMED
    failure_code: str | None = None
    retry_after: timedelta | None = None
    try:
        provider_message_id = await transport.send_reply(prepared.message)
    except KnownDeliveryFailure as error:
        provider_message_id = None
        failure_code = error.code
        retry_after = error.retry_after
        outcome = DeliveryOutcomeKind.BLOCKED if error.blocked else DeliveryOutcomeKind.FAILED
    except UnknownDeliveryFailure as error:
        provider_message_id = None
        failure_code = error.code
        outcome = DeliveryOutcomeKind.UNKNOWN
    except Exception as error:
        provider_message_id = None
        failure_code = f"transport_error:{type(error).__name__}"[:80]
        outcome = DeliveryOutcomeKind.UNKNOWN

    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            attempt_record = await session.scalar(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.id == prepared.attempt_id)
                .with_for_update()
            )
            event = await session.get(OutboxEvent, prepared.reply_event_id)
            if attempt_record is None or event is None or attempt_record.outcome != "started":
                raise RuntimeError("reply attempt start receipt is missing or already final")
            attempt_record.outcome = outcome.value
            attempt_record.failure_code = failure_code
            attempt_record.provider_message_id = provider_message_id
            attempt_record.finished_at = now().astimezone(UTC)
            result = _reply_outcome_from_record(event, attempt_record)
            await _publish_outcome(
                session, result, attempt_number=attempt_record.attempt_number
            )
            if outcome is DeliveryOutcomeKind.FAILED and retry_after is not None:
                await fail_job(
                    session,
                    claim,
                    error=failure_code or "known_transient_failure",
                    retry_after=retry_after,
                )
            else:
                await complete_job(session, claim)
    return result


async def deliver_recovery_summary_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    transport: ReminderTransport,
    *,
    identity_store_factory: IdentityStoreFactory,
    now: Callable[[], datetime],
) -> DeliveryAttemptOutcome | None:
    """Send one current owner-scoped overdue summary without starting check-in cycles."""
    if claim.lane is not JobLane.DELIVERY or claim.kind != DELIVERY_RECOVERY_SUMMARY_JOB_KIND:
        raise ValueError("claim is not a recovery summary delivery job")
    try:
        owner_id = UUID(str(claim.payload["owner_id"]))
        request = RecoverySummaryRequest(owner_id, str(claim.payload["episode_key"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("recovery summary job has an invalid payload") from error
    if owner_id != claim.owner_id:
        raise ValueError("recovery summary owner does not match its leased job")

    prepared: _PreparedRecoverySummary | None = None
    immediate: DeliveryAttemptOutcome | None = None
    async with sessions() as session:
        async with session.begin():
            await lock_job_for_effects(session, claim)
            prior = await _latest_recovery_attempt(session, owner_id, request.episode_key)
            if prior is not None and prior.outcome == "started":
                immediate = await _finish_recovery_as_unknown(session, claim, prior, at=now())
            elif prior is not None and prior.outcome in _TERMINAL_OUTCOMES:
                immediate = _recovery_outcome_from_record(prior)
                await complete_job(session, claim)
            else:
                destination = await transport.resolve_destination(session, owner_id)
                identity = identity_store_factory(session)
                preferences = await identity.lock_preferences(owner_id)
                overdue = (
                    await SqlAlchemyAgendaReadStore(session).list_overdue(
                        owner_id,
                        as_of=now(),
                        display_zone=preferences.home_zone,
                        limit=100,
                        for_update=True,
                    )
                    if preferences is not None
                    else ()
                )
                access_store = SqlAlchemyAccessUsageStore(session)
                lifecycle = await access_store.lock_account_lifecycle(owner_id)
                access = await access_store.lock_access(
                    CheckAccess(owner_id, Operation.REMINDER_DELIVERY, now())
                )
                if destination is None:
                    immediate = await _record_recovery_outcome(
                        session,
                        request,
                        target_id=None,
                        attempt_number=await _next_recovery_attempt_number(
                            session, owner_id, request.episode_key
                        ),
                        outcome=DeliveryOutcomeKind.BLOCKED,
                        at=now(),
                        failure_code="destination_missing",
                    )
                    await complete_job(session, claim)
                elif lifecycle is not AccountLifecycle.PRESENT:
                    immediate = await _record_recovery_outcome(
                        session,
                        request,
                        target_id=destination,
                        attempt_number=await _next_recovery_attempt_number(
                            session, owner_id, request.episode_key
                        ),
                        outcome=DeliveryOutcomeKind.SKIPPED,
                        at=now(),
                        failure_code=(
                            "owner_deleted"
                            if lifecycle is AccountLifecycle.DELETED
                            else "owner_not_present"
                        ),
                    )
                    await complete_job(session, claim)
                elif not access.allowed:
                    immediate = await _record_recovery_outcome(
                        session,
                        request,
                        target_id=destination,
                        attempt_number=await _next_recovery_attempt_number(
                            session, owner_id, request.episode_key
                        ),
                        outcome=DeliveryOutcomeKind.SKIPPED,
                        at=now(),
                        failure_code=(access.denial.value if access.denial else "access_denied"),
                        access_period_id=access.period_id,
                    )
                    await complete_job(session, claim)
                elif preferences is None:
                    immediate = await _record_recovery_outcome(
                        session,
                        request,
                        target_id=destination,
                        attempt_number=await _next_recovery_attempt_number(
                            session, owner_id, request.episode_key
                        ),
                        outcome=DeliveryOutcomeKind.SKIPPED,
                        at=now(),
                        failure_code="preferences_missing",
                        access_period_id=access.period_id,
                    )
                    await complete_job(session, claim)
                elif await _destination_blocked(session, owner_id, destination):
                    immediate = await _record_recovery_outcome(
                        session,
                        request,
                        target_id=destination,
                        attempt_number=await _next_recovery_attempt_number(
                            session, owner_id, request.episode_key
                        ),
                        outcome=DeliveryOutcomeKind.BLOCKED,
                        at=now(),
                        failure_code="destination_previously_blocked",
                        access_period_id=access.period_id,
                    )
                    await complete_job(session, claim)
                else:
                    quiet_end = _quiet_end_at(
                        now(),
                        preferences.home_zone,
                        preferences.quiet_start,
                        preferences.quiet_end,
                    )
                    if quiet_end is not None:
                        await fail_job(
                            session,
                            claim,
                            error="quiet_hours_deferred",
                            retry_after=max(quiet_end - now().astimezone(UTC), timedelta(0)),
                        )
                    else:
                        if not overdue:
                            await complete_job(session, claim)
                        else:
                            items = tuple(
                                RecoverySummaryItem(
                                    row.title,
                                    _overdue_label(row, preferences.home_zone),
                                )
                                for row in overdue
                            )
                            summary = render_recovery_summary(items, preferences.language)
                            await lock_job_for_effects(session, claim)
                            authorized_at = now()
                            latest_access = await access_store.lock_access(
                                CheckAccess(
                                    owner_id,
                                    Operation.REMINDER_DELIVERY,
                                    authorized_at,
                                )
                            )
                            if not latest_access.allowed:
                                immediate = await _record_recovery_outcome(
                                    session,
                                    request,
                                    target_id=destination,
                                    attempt_number=await _next_recovery_attempt_number(
                                        session, owner_id, request.episode_key
                                    ),
                                    outcome=DeliveryOutcomeKind.SKIPPED,
                                    at=authorized_at,
                                    failure_code=(
                                        latest_access.denial.value
                                        if latest_access.denial is not None
                                        else "access_denied"
                                    ),
                                    access_period_id=latest_access.period_id,
                                )
                                await complete_job(session, claim)
                            elif (
                                boundary_quiet_end := _quiet_end_at(
                                    authorized_at,
                                    preferences.home_zone,
                                    preferences.quiet_start,
                                    preferences.quiet_end,
                                )
                            ) is not None:
                                await fail_job(
                                    session,
                                    claim,
                                    error="quiet_hours_deferred",
                                    retry_after=max(
                                        boundary_quiet_end - authorized_at.astimezone(UTC),
                                        timedelta(0),
                                    ),
                                )
                            else:
                                attempt_number = await _next_recovery_attempt_number(
                                    session, owner_id, request.episode_key
                                )
                                attempt = DeliveryAttemptRecord(
                                    id=uuid4(),
                                    owner_id=owner_id,
                                    recovery_episode_key=request.episode_key,
                                    target_id=destination,
                                    dispatch_version=1,
                                    attempt_number=attempt_number,
                                    access_period_id=latest_access.period_id,
                                    outcome="started",
                                    started_at=authorized_at.astimezone(UTC),
                                )
                                session.add(attempt)
                                await session.flush()
                                prepared = _PreparedRecoverySummary(
                                    attempt.id,
                                    owner_id,
                                    request.episode_key,
                                    destination,
                                    attempt_number,
                                    TelegramReplySend(
                                        destination,
                                        summary,
                                        summary,
                                        parse_mode=None,
                                    ),
                                )

    if prepared is None:
        return immediate

    outcome = DeliveryOutcomeKind.CONFIRMED
    failure_code: str | None = None
    retry_after: timedelta | None = None
    try:
        await transport.send_reply(prepared.message)
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
            finished_attempt = await session.scalar(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.id == prepared.attempt_id)
                .with_for_update()
            )
            if finished_attempt is None or finished_attempt.outcome != "started":
                raise RuntimeError("recovery summary start receipt is missing or already final")
            finished_attempt.outcome = outcome.value
            finished_attempt.failure_code = failure_code
            finished_attempt.finished_at = now().astimezone(UTC)
            result = _recovery_outcome_from_record(finished_attempt)
            await _publish_outcome(
                session, result, attempt_number=finished_attempt.attempt_number
            )
            if outcome is DeliveryOutcomeKind.FAILED and retry_after is not None:
                await fail_job(
                    session,
                    claim,
                    error=failure_code or "known_transient_failure",
                    retry_after=retry_after,
                )
            else:
                await complete_job(session, claim)
    return result


def _positive_int(value: object) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


def _event_envelope(event: OutboxEvent) -> EventEnvelope | None:
    if not isinstance(event.payload, dict):
        return None
    try:
        envelope = EventEnvelope.from_dict(event.payload)
    except (KeyError, TypeError, ValueError):
        return None
    if envelope.id != event.id or envelope.event_type != event.event_type:
        return None
    return envelope


def _reply_job_key(event_id: UUID) -> str:
    return f"reply:{event_id}"


async def _latest_reply_attempt(
    session: AsyncSession, event_id: UUID, target_id: int
) -> DeliveryAttemptRecord | None:
    return cast(
        DeliveryAttemptRecord | None,
        await session.scalar(
            select(DeliveryAttemptRecord)
            .where(
                DeliveryAttemptRecord.reply_event_id == event_id,
                DeliveryAttemptRecord.dispatch_version == 1,
                DeliveryAttemptRecord.target_id == target_id,
            )
            .order_by(DeliveryAttemptRecord.attempt_number.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _next_reply_attempt_number(
    session: AsyncSession, event_id: UUID, target_id: int | None
) -> int:
    highest = await session.scalar(
        select(func.max(DeliveryAttemptRecord.attempt_number)).where(
            DeliveryAttemptRecord.reply_event_id == event_id,
            DeliveryAttemptRecord.dispatch_version == 1,
            DeliveryAttemptRecord.target_id == target_id,
        )
    )
    return int(highest or 0) + 1


async def _record_reply_outcome(
    session: AsyncSession,
    event: OutboxEvent,
    *,
    target_id: int | None,
    attempt_number: int,
    outcome: DeliveryOutcomeKind,
    at: datetime,
    failure_code: str | None,
) -> DeliveryAttemptOutcome:
    at = at.astimezone(UTC)
    attempt = DeliveryAttemptRecord(
        id=uuid4(),
        owner_id=event.owner_id,
        reply_event_id=event.id,
        target_id=target_id,
        dispatch_version=1,
        attempt_number=attempt_number,
        outcome=outcome.value,
        failure_code=failure_code,
        started_at=at,
        finished_at=at,
    )
    session.add(attempt)
    await session.flush()
    result = _reply_outcome_from_record(event, attempt)
    await _publish_outcome(session, result, attempt_number=attempt_number)
    return result


async def _finish_reply_as_unknown(
    session: AsyncSession,
    claim: JobClaim,
    event: OutboxEvent,
    attempt: DeliveryAttemptRecord,
    *,
    at: datetime,
) -> DeliveryAttemptOutcome:
    attempt.outcome = DeliveryOutcomeKind.UNKNOWN.value
    attempt.failure_code = "worker_lost_after_send_started"
    attempt.finished_at = at.astimezone(UTC)
    result = _reply_outcome_from_record(event, attempt)
    await _publish_outcome(session, result, attempt_number=attempt.attempt_number)
    await complete_job(session, claim)
    return result


def _reply_outcome_from_record(
    event: OutboxEvent, attempt: DeliveryAttemptRecord
) -> DeliveryAttemptOutcome:
    return DeliveryAttemptOutcome(
        intent_id=None,
        owner_id=event.owner_id,
        dispatch_version=1,
        target_id=attempt.target_id,
        attempt_id=attempt.id,
        outcome=DeliveryOutcomeKind(attempt.outcome),
        occurred_at=(attempt.finished_at or attempt.started_at).astimezone(UTC),
        failure_code=attempt.failure_code,
        reply_event_id=event.id,
        quiet_conflict=attempt.quiet_conflict,
    )


async def _latest_recovery_attempt(
    session: AsyncSession, owner_id: UUID, episode_key: str
) -> DeliveryAttemptRecord | None:
    return cast(
        DeliveryAttemptRecord | None,
        await session.scalar(
            select(DeliveryAttemptRecord)
            .where(
                DeliveryAttemptRecord.owner_id == owner_id,
                DeliveryAttemptRecord.recovery_episode_key == episode_key,
                DeliveryAttemptRecord.dispatch_version == 1,
            )
            .order_by(DeliveryAttemptRecord.attempt_number.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _next_recovery_attempt_number(
    session: AsyncSession, owner_id: UUID, episode_key: str
) -> int:
    highest = await session.scalar(
        select(func.max(DeliveryAttemptRecord.attempt_number)).where(
            DeliveryAttemptRecord.owner_id == owner_id,
            DeliveryAttemptRecord.recovery_episode_key == episode_key,
            DeliveryAttemptRecord.dispatch_version == 1,
        )
    )
    return int(highest or 0) + 1


async def _record_recovery_outcome(
    session: AsyncSession,
    request: RecoverySummaryRequest,
    *,
    target_id: int | None,
    attempt_number: int,
    outcome: DeliveryOutcomeKind,
    at: datetime,
    failure_code: str | None,
    access_period_id: str | None = None,
) -> DeliveryAttemptOutcome:
    at = at.astimezone(UTC)
    attempt = DeliveryAttemptRecord(
        id=uuid4(),
        owner_id=request.owner_id,
        recovery_episode_key=request.episode_key,
        target_id=target_id,
        dispatch_version=1,
        attempt_number=attempt_number,
        access_period_id=access_period_id,
        outcome=outcome.value,
        failure_code=failure_code,
        started_at=at,
        finished_at=at,
    )
    session.add(attempt)
    await session.flush()
    result = _recovery_outcome_from_record(attempt)
    await _publish_outcome(session, result, attempt_number=attempt_number)
    return result


async def _finish_recovery_as_unknown(
    session: AsyncSession,
    claim: JobClaim,
    attempt: DeliveryAttemptRecord,
    *,
    at: datetime,
) -> DeliveryAttemptOutcome:
    attempt.outcome = DeliveryOutcomeKind.UNKNOWN.value
    attempt.failure_code = "worker_lost_after_send_started"
    attempt.finished_at = at.astimezone(UTC)
    result = _recovery_outcome_from_record(attempt)
    await _publish_outcome(session, result, attempt_number=attempt.attempt_number)
    await complete_job(session, claim)
    return result


def _recovery_outcome_from_record(
    attempt: DeliveryAttemptRecord,
) -> DeliveryAttemptOutcome:
    if attempt.recovery_episode_key is None:
        raise ValueError("attempt is not a recovery-summary attempt")
    return DeliveryAttemptOutcome(
        intent_id=None,
        owner_id=attempt.owner_id,
        dispatch_version=attempt.dispatch_version,
        target_id=attempt.target_id,
        attempt_id=attempt.id,
        outcome=DeliveryOutcomeKind(attempt.outcome),
        occurred_at=(attempt.finished_at or attempt.started_at).astimezone(UTC),
        failure_code=attempt.failure_code,
        access_period_id=attempt.access_period_id,
        recovery_episode_key=attempt.recovery_episode_key,
    )


def _recovery_summary_job_key(owner_id: UUID, episode_key: str) -> str:
    return f"recovery-summary:{owner_id}:{episode_key}"


def _overdue_label(record: AgendaRecord, zone_name: str) -> str:
    if record.kind == "untimed":
        return record.local_date.isoformat()
    instant = (
        record.ends_at
        if record.kind == "appointment"
        else record.deadline_at
        if record.kind == "deadline"
        else record.starts_at
    )
    if instant is None:
        return record.local_date.isoformat()
    return instant.astimezone(ZoneInfo(zone_name)).strftime("%Y-%m-%d %H:%M")


def _job_key(intent_id: UUID, dispatch_version: int) -> str:
    return f"reminder:{intent_id}:{dispatch_version}"


def _job_payload(claim: JobClaim) -> tuple[UUID, int, UUID | None]:
    payload = claim.payload
    try:
        intent_id = UUID(str(payload["intent_id"]))
        dispatch_version = int(payload["dispatch_version"])
        raw_cycle = payload.get("check_in_cycle_id")
        cycle_id = UUID(str(raw_cycle)) if raw_cycle is not None else None
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("reminder delivery job has an invalid payload") from error
    if dispatch_version < 1:
        raise ValueError("dispatch_version must be positive")
    return intent_id, dispatch_version, cycle_id


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("delivery clock must be timezone-aware")


async def _current_schedule(
    session: AsyncSession, intent: ReminderIntentRecord, identity: IdentityStore
) -> tuple[PreferencesSnapshot, CurrentOccurrenceSchedule] | None:
    event = await session.get(OutboxEvent, intent.source_event_id)
    if event is None or event.owner_id != intent.owner_id:
        return None
    try:
        change = ScheduleChange(
            event.id,
            event.event_type,
            event.aggregate_id,
            event.aggregate_version,
            intent.owner_id,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    current: CurrentPlanSchedule | None = (
        await SqlAlchemyReminderScheduleStore(session, identity).lock_current_schedule(change)
    )
    if (
        current is None
        or current.owner_id != intent.owner_id
        or current.plan_item_id != intent.plan_item_id
        or current.plan_version != intent.plan_version
        or current.preferences_version != intent.preferences_version
    ):
        return None
    occurrence = next(
        (item for item in current.occurrences if item.occurrence_id == intent.occurrence_id),
        None,
    )
    if (
        occurrence is None
        or occurrence.state != "open"
        or occurrence.occurrence_version != intent.occurrence_version
        or occurrence.approved_preferences_version != intent.preferences_version
        or occurrence.series_id != intent.series_id
        or occurrence.series_version != intent.series_version
    ):
        return None
    if not any(
        candidate.offset_kind == intent.offset_kind
        and candidate.offset_amount == intent.offset_amount
        and candidate.due_at.astimezone(UTC) == intent.due_at.astimezone(UTC)
        and candidate.source == intent.source
        for candidate in occurrence.reminders
    ):
        return None
    preferences = await identity.get_preferences(intent.owner_id)
    if preferences is None or preferences.version != intent.preferences_version:
        return None
    return preferences, occurrence


async def _latest_attempt(
    session: AsyncSession, intent: ReminderIntentRecord, target_id: int
) -> DeliveryAttemptRecord | None:
    return cast(
        DeliveryAttemptRecord | None,
        await session.scalar(
            select(DeliveryAttemptRecord)
            .where(
                DeliveryAttemptRecord.intent_id == intent.id,
                DeliveryAttemptRecord.dispatch_version == intent.dispatch_version,
                DeliveryAttemptRecord.target_id == target_id,
            )
            .order_by(DeliveryAttemptRecord.attempt_number.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _latest_attempt_for_version(
    session: AsyncSession, intent_id: UUID, version: int, target_id: int
) -> DeliveryAttemptRecord | None:
    return cast(
        DeliveryAttemptRecord | None,
        await session.scalar(
            select(DeliveryAttemptRecord)
            .where(
                DeliveryAttemptRecord.intent_id == intent_id,
                DeliveryAttemptRecord.dispatch_version == version,
                DeliveryAttemptRecord.target_id == target_id,
            )
            .order_by(DeliveryAttemptRecord.attempt_number.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def _next_attempt_number(
    session: AsyncSession, intent: ReminderIntentRecord, target_id: int | None
) -> int:
    highest = await session.scalar(
        select(func.max(DeliveryAttemptRecord.attempt_number)).where(
            DeliveryAttemptRecord.intent_id == intent.id,
            DeliveryAttemptRecord.dispatch_version == intent.dispatch_version,
            DeliveryAttemptRecord.target_id == target_id,
        )
    )
    return int(highest or 0) + 1


async def _destination_blocked(
    session: AsyncSession, owner_id: UUID, target_id: int
) -> bool:
    return (
        await session.scalar(
            select(DeliveryAttemptRecord.id)
            .where(
                DeliveryAttemptRecord.owner_id == owner_id,
                DeliveryAttemptRecord.target_id == target_id,
                DeliveryAttemptRecord.outcome == DeliveryOutcomeKind.BLOCKED.value,
            )
            .limit(1)
        )
        is not None
    )


async def _record_outcome(
    session: AsyncSession,
    intent: ReminderIntentRecord,
    *,
    target_id: int | None,
    attempt_number: int,
    cycle_id: UUID | None,
    outcome: DeliveryOutcomeKind,
    at: datetime,
    failure_code: str | None,
    access_period_id: str | None = None,
) -> DeliveryAttemptOutcome:
    at = at.astimezone(UTC)
    attempt = DeliveryAttemptRecord(
        id=uuid4(),
        owner_id=intent.owner_id,
        intent_id=intent.id,
        target_id=target_id,
        dispatch_version=intent.dispatch_version,
        attempt_number=attempt_number,
        check_in_cycle_id=cycle_id,
        access_period_id=access_period_id,
        outcome=outcome.value,
        failure_code=failure_code,
        started_at=at,
        finished_at=at,
    )
    session.add(attempt)
    await session.flush()
    result = _outcome_from_record(intent, attempt)
    await _publish_outcome(session, result, attempt_number=attempt_number)
    return result


async def _publish_outcome(
    session: AsyncSession, result: DeliveryAttemptOutcome, *, attempt_number: int
) -> None:
    await add_outbox_event(
        session,
        EventEnvelope(
            id=uuid4(),
            event_type="notifications.delivery_attempt_outcome",
            schema_version=1,
            aggregate_id=result.attempt_id,
            aggregate_version=1,
            owner_id=result.owner_id,
            occurred_at=result.occurred_at,
            payload={
                "intent_id": str(result.intent_id) if result.intent_id else None,
                "reply_event_id": (
                    str(result.reply_event_id) if result.reply_event_id else None
                ),
                "recovery_episode_key": result.recovery_episode_key,
                "target_id": result.target_id,
                "dispatch_version": result.dispatch_version,
                "attempt_id": str(result.attempt_id),
                "attempt_number": attempt_number,
                "outcome": result.outcome.value,
                "failure_code": result.failure_code,
                "check_in_cycle_id": (
                    str(result.check_in_cycle_id) if result.check_in_cycle_id else None
                ),
                "access_period_id": result.access_period_id,
                "quiet_conflict": result.quiet_conflict,
            },
        ),
    )


def _outcome_from_record(
    intent: ReminderIntentRecord | _PreparedSend, attempt: DeliveryAttemptRecord
) -> DeliveryAttemptOutcome:
    return DeliveryAttemptOutcome(
        intent_id=intent.id if isinstance(intent, ReminderIntentRecord) else intent.intent_id,
        owner_id=intent.owner_id,
        dispatch_version=(
            intent.dispatch_version
            if isinstance(intent, ReminderIntentRecord)
            else intent.dispatch_version
        ),
        target_id=attempt.target_id,
        attempt_id=attempt.id,
        outcome=DeliveryOutcomeKind(attempt.outcome),
        occurred_at=(attempt.finished_at or attempt.started_at).astimezone(UTC),
        failure_code=attempt.failure_code,
        check_in_cycle_id=attempt.check_in_cycle_id,
        access_period_id=attempt.access_period_id,
        quiet_conflict=attempt.quiet_conflict,
    )


def _intent_stub(prepared: _PreparedSend) -> _PreparedSend:
    return prepared


async def _finish_existing_as_unknown(
    session: AsyncSession, claim: JobClaim, attempt: DeliveryAttemptRecord, *, at: datetime
) -> DeliveryAttemptOutcome:
    attempt.outcome = DeliveryOutcomeKind.UNKNOWN.value
    attempt.failure_code = "worker_lost_after_send_started"
    attempt.finished_at = at.astimezone(UTC)
    intent = await session.get(ReminderIntentRecord, attempt.intent_id)
    if intent is None:
        raise RuntimeError("started reminder intent disappeared before recovery")
    result = _outcome_from_record(intent, attempt)
    await _publish_outcome(session, result, attempt_number=attempt.attempt_number)
    await complete_job(session, claim)
    return result


async def _defer_if_quiet(
    session: AsyncSession,
    claim: JobClaim,
    intent: ReminderIntentRecord,
    target_id: int,
    cycle_id: UUID | None,
    zone_name: str,
    quiet_start: time,
    quiet_end: time,
    event_start_at: datetime | None,
    access_period_id: str | None,
    at: datetime,
) -> bool:
    if intent.source == "explicit":
        return False
    quiet_end_at = _quiet_end_at(at, zone_name, quiet_start, quiet_end)
    if quiet_end_at is None:
        return False
    useful = event_start_at is not None and quiet_end_at < event_start_at.astimezone(UTC)
    outcome = (
        DeliveryOutcomeKind.DEFERRED if useful else DeliveryOutcomeKind.SKIPPED
    )
    await _record_outcome(
        session,
        intent,
        target_id=target_id,
        attempt_number=await _next_attempt_number(session, intent, target_id),
        cycle_id=cycle_id,
        outcome=outcome,
        at=at,
        failure_code="quiet_hours_deferred" if useful else "quiet_hours_no_longer_useful",
        access_period_id=access_period_id,
    )
    if useful:
        intent.not_before_at = quiet_end_at
        intent.dispatch_version += 1
    await complete_job(session, claim)
    return True


def _quiet_end_at(at: datetime, zone_name: str, start: time, end: time) -> datetime | None:
    local = at.astimezone(ZoneInfo(zone_name))
    local_time = local.timetz().replace(tzinfo=None)
    if start == end:
        return None
    if start < end:
        in_quiet = start <= local_time < end
        end_date = local.date()
    else:
        in_quiet = local_time >= start or local_time < end
        end_date = local.date() + timedelta(days=1) if local_time >= start else local.date()
    if not in_quiet:
        return None
    return datetime.combine(end_date, end, ZoneInfo(zone_name)).astimezone(UTC)


async def _coalesce_if_older(
    session: AsyncSession,
    claim: JobClaim,
    intent: ReminderIntentRecord,
    target_id: int,
    cycle_id: UUID | None,
    access_period_id: str | None,
    at: datetime,
) -> bool:
    siblings = list(
        (
            await session.scalars(
                select(ReminderIntentRecord)
                .where(
                    ReminderIntentRecord.owner_id == intent.owner_id,
                    ReminderIntentRecord.occurrence_id == intent.occurrence_id,
                    ReminderIntentRecord.state == "scheduled",
                    func.coalesce(
                        ReminderIntentRecord.not_before_at, ReminderIntentRecord.due_at
                    )
                    <= at.astimezone(UTC),
                )
                .order_by(ReminderIntentRecord.due_at, ReminderIntentRecord.id)
                .with_for_update()
            )
        ).all()
    )
    valid_due: list[ReminderIntentRecord] = []
    for row in siblings:
        if row.id == intent.id:
            continue
        if (
            row.plan_version != intent.plan_version
            or row.occurrence_version != intent.occurrence_version
            or row.preferences_version != intent.preferences_version
            or row.series_version != intent.series_version
        ):
            continue
        valid_due.append(row)
    latest_due = max(valid_due, key=lambda row: (row.due_at, str(row.id)), default=None)
    if latest_due is not None and latest_due.due_at > intent.due_at:
        await _record_outcome(
            session,
            intent,
            target_id=target_id,
            attempt_number=await _next_attempt_number(session, intent, target_id),
            cycle_id=cycle_id,
            outcome=DeliveryOutcomeKind.COALESCED,
            at=at,
            failure_code="newer_due_reminder_selected",
            access_period_id=access_period_id,
        )
        await complete_job(session, claim)
        return True
    # The current reminder is the useful latest one; older due siblings are skipped
    # by their own pre-send check, which observes this reminder while it stays scheduled.
    return False


