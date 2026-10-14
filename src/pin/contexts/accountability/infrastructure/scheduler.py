"""Idempotent scheduling policy for appointment checks and daily reviews."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.application.contracts import (
    CurrentOccurrence,
    ReviewProfile,
    TodayUntimedReader,
)
from pin.contexts.accountability.domain.cycles import (
    CycleState,
    MessageSlot,
    appointment_check_due,
    review_due_at,
)
from pin.contexts.accountability.domain.reviews import ReviewItem, review_items_for_today
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityIntentRecord,
    AccountabilityOutcomeReceiptRecord,
    CheckInCycleRecord,
    DailyReviewRecord,
)
from pin.contexts.notifications.api import defer_until_quiet_hours_end
from pin.platform.jobs import JobLane, enqueue_job

DELIVERY_JOB_KIND = "accountability.deliver"


async def synchronize_occurrence(
    session: AsyncSession,
    occurrence: CurrentOccurrence,
    *,
    now: datetime,
) -> CheckInCycleRecord | None:
    """Close obsolete versions and create a cycle only from a persisted due instant."""
    _require_aware(now)
    if occurrence.state != "open":
        await _close_occurrence_cycles(
            session,
            occurrence.owner_id,
            occurrence.occurrence_id,
            CycleState.COMPLETED if occurrence.state == "completed" else CycleState.CANCELLED,
        )
        return None
    due_at = appointment_check_due(
        kind=occurrence.kind, end_at=occurrence.ends_at, deadline_at=occurrence.deadline_at
    )
    if due_at is None:
        await _close_occurrence_cycles(
            session, occurrence.owner_id, occurrence.occurrence_id, CycleState.SUPERSEDED
        )
        return None
    prior = list(
        (
            await session.scalars(
                select(CheckInCycleRecord)
                .where(
                    CheckInCycleRecord.owner_id == occurrence.owner_id,
                    CheckInCycleRecord.occurrence_id == occurrence.occurrence_id,
                )
                .order_by(CheckInCycleRecord.occurrence_version.desc())
                .with_for_update()
            )
        ).all()
    )
    for cycle in prior:
        if cycle.occurrence_version == occurrence.occurrence_version:
            if cycle.state != CycleState.OPEN.value:
                return cycle
            cycle.title = occurrence.title
            cycle.expected_end_at = due_at
            return cycle
        if cycle.state == CycleState.OPEN.value:
            cycle.state = CycleState.SUPERSEDED.value
    cycle = CheckInCycleRecord(
        id=uuid4(),
        owner_id=occurrence.owner_id,
        occurrence_id=occurrence.occurrence_id,
        occurrence_version=occurrence.occurrence_version,
        kind=occurrence.kind,
        title=occurrence.title,
        expected_end_at=due_at,
        state=CycleState.OPEN.value,
        nudge_slot_used=False,
    )
    session.add(cycle)
    await session.flush()
    return cycle


async def schedule_due_accountability(
    session: AsyncSession,
    *,
    profiles: tuple[ReviewProfile, ...],
    today_untimed: TodayUntimedReader,
    now: datetime,
    limit: int = 100,
) -> int:
    """Create stable intent/job pairs for due cycles, nudges and profile reviews."""
    _require_aware(now)
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    created = 0
    owner_ids = tuple(profile.owner_id for profile in profiles)
    now_utc = now.astimezone(UTC)
    if owner_ids:
        initial_exists = exists(
            select(1).where(
                AccountabilityIntentRecord.cycle_id == CheckInCycleRecord.id,
                AccountabilityIntentRecord.slot == MessageSlot.INITIAL.value,
            )
        )
        due_initial = and_(
            CheckInCycleRecord.expected_end_at <= now_utc,
            CheckInCycleRecord.initial_confirmed_at.is_(None),
            ~initial_exists,
        )
        due_nudge = and_(
            CheckInCycleRecord.initial_confirmed_at.is_not(None),
            CheckInCycleRecord.nudge_due_at <= now_utc,
            CheckInCycleRecord.nudge_slot_used.is_(False),
        )
        cycles = list(
            (
                await session.scalars(
                    select(CheckInCycleRecord)
                    .where(
                        CheckInCycleRecord.state == CycleState.OPEN.value,
                        CheckInCycleRecord.owner_id.in_(owner_ids),
                        or_(due_initial, due_nudge),
                    )
                    .order_by(CheckInCycleRecord.expected_end_at, CheckInCycleRecord.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
    else:
        cycles = []
    for cycle in cycles:
        profile = next((item for item in profiles if item.owner_id == cycle.owner_id), None)
        if profile is None:
            continue
        initial = await _get_intent(session, cycle_id=cycle.id, slot=MessageSlot.INITIAL)
        if initial is None and cycle.expected_end_at <= now.astimezone(UTC):
            due = defer_until_quiet_hours_end(
                now,
                zone_name=profile.home_zone,
                quiet_start=profile.quiet_start,
                quiet_end=profile.quiet_end,
            )
            initial = await _new_intent(
                session,
                owner_id=cycle.owner_id,
                cycle_id=cycle.id,
                slot=MessageSlot.INITIAL,
                due_at=due,
            )
            created += 1
        if initial is not None and initial.state == "scheduled":
            await _enqueue_send(session, initial, cycle.owner_id)
        if cycle.initial_confirmed_at is not None and not cycle.nudge_slot_used:
            nudge_due = cycle.nudge_due_at
            if nudge_due is not None and nudge_due <= now.astimezone(UTC):
                intent = await _get_intent(session, cycle_id=cycle.id, slot=MessageSlot.NUDGE)
                if intent is None:
                    intent = await _new_intent(
                        session,
                        owner_id=cycle.owner_id,
                        cycle_id=cycle.id,
                        slot=MessageSlot.NUDGE,
                        due_at=max(
                            nudge_due,
                            defer_until_quiet_hours_end(
                                nudge_due,
                                zone_name=profile.home_zone,
                                quiet_start=profile.quiet_start,
                                quiet_end=profile.quiet_end,
                            ),
                        ),
                    )
                    cycle.nudge_slot_used = True
                    cycle.nudge_intent_id = intent.id
                    created += 1
                await _enqueue_send(session, intent, cycle.owner_id)

    for profile in profiles:
        local_today = now.astimezone(ZoneInfo(profile.home_zone)).date()
        review_due = review_due_at(local_today, profile.review_time, profile.home_zone)
        if review_due > now.astimezone(UTC):
            continue
        existing = await session.scalar(
            select(DailyReviewRecord)
            .where(
                DailyReviewRecord.owner_id == profile.owner_id,
                DailyReviewRecord.review_date == local_today,
            )
            .with_for_update()
        )
        if existing is None:
            current = await today_untimed.list_today_open_untimed(
                profile.owner_id, profile_zone=profile.home_zone, at=now
            )
            grouped = review_items_for_today(
                tuple(
                    # The reader already filters state, type and profile-local date.
                    ReviewItem(item.occurrence_id, item.title)
                    for item in current
                    if item.local_date == local_today
                )
            )
            if not grouped:
                continue
            existing = DailyReviewRecord(
                id=uuid4(),
                owner_id=profile.owner_id,
                review_date=local_today,
                zone_name=profile.home_zone,
                language=profile.language,
                destination_id=profile.destination_id,
                occurrence_ids=[str(item.occurrence_id) for item in grouped],
                state=CycleState.OPEN.value,
                due_at=review_due,
                nudge_slot_used=False,
            )
            session.add(existing)
            await session.flush()
        if existing.state != CycleState.OPEN.value:
            continue
        initial = await _get_intent(session, review_id=existing.id, slot=MessageSlot.INITIAL)
        if initial is None:
            initial = await _new_intent(
                session,
                owner_id=profile.owner_id,
                review_id=existing.id,
                slot=MessageSlot.INITIAL,
                due_at=defer_until_quiet_hours_end(
                    now,
                    zone_name=profile.home_zone,
                    quiet_start=profile.quiet_start,
                    quiet_end=profile.quiet_end,
                ),
            )
            created += 1
        if initial is not None and initial.state == "scheduled":
            await _enqueue_send(session, initial, profile.owner_id)
        if existing.initial_confirmed_at is not None and not existing.nudge_slot_used:
            nudge_due = existing.nudge_due_at
            if nudge_due is not None and nudge_due <= now.astimezone(UTC):
                intent = await _get_intent(session, review_id=existing.id, slot=MessageSlot.NUDGE)
                if intent is None:
                    intent = await _new_intent(
                        session,
                        owner_id=profile.owner_id,
                        review_id=existing.id,
                        slot=MessageSlot.NUDGE,
                        due_at=max(
                            nudge_due,
                            defer_until_quiet_hours_end(
                                nudge_due,
                                zone_name=profile.home_zone,
                                quiet_start=profile.quiet_start,
                                quiet_end=profile.quiet_end,
                            ),
                        ),
                    )
                    existing.nudge_slot_used = True
                    existing.nudge_intent_id = intent.id
                    created += 1
                await _enqueue_send(session, intent, profile.owner_id)
    return created


async def apply_delivery_outcome(
    session: AsyncSession,
    *,
    event_id: UUID,
    owner_id: UUID,
    intent_id: UUID,
    outcome: str,
    occurred_at: datetime,
    attempt_number: int,
    retrying: bool = False,
) -> bool:
    """Apply one durable Delivery outcome without restarting or reviving a cycle."""
    _require_aware(occurred_at)
    if attempt_number < 1:
        raise ValueError("delivery attempt number must be positive")
    if outcome not in {"confirmed", "failed", "unknown", "blocked"}:
        raise ValueError("delivery outcome is not supported for Accountability")
    receipt = await session.scalar(
        select(AccountabilityOutcomeReceiptRecord).where(
            AccountabilityOutcomeReceiptRecord.owner_id == owner_id,
            AccountabilityOutcomeReceiptRecord.event_id == event_id,
        )
    )
    if receipt is not None:
        return False
    candidate = (
        await session.execute(
            select(
                AccountabilityIntentRecord.cycle_id,
                AccountabilityIntentRecord.review_id,
            ).where(
                AccountabilityIntentRecord.id == intent_id,
                AccountabilityIntentRecord.owner_id == owner_id,
            )
        )
    ).one_or_none()
    if candidate is None:
        session.add(
            AccountabilityOutcomeReceiptRecord(id=uuid4(), owner_id=owner_id, event_id=event_id)
        )
        await session.flush()
        return True
    cycle_id, review_id = candidate
    cycle = None
    review = None
    if cycle_id is not None:
        cycle = await session.scalar(
            select(CheckInCycleRecord)
            .where(CheckInCycleRecord.id == cycle_id, CheckInCycleRecord.owner_id == owner_id)
            .with_for_update()
        )
    elif review_id is not None:
        review = await session.scalar(
            select(DailyReviewRecord)
            .where(DailyReviewRecord.id == review_id, DailyReviewRecord.owner_id == owner_id)
            .with_for_update()
        )
    intent = await session.scalar(
        select(AccountabilityIntentRecord)
        .where(
            AccountabilityIntentRecord.id == intent_id,
            AccountabilityIntentRecord.owner_id == owner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session.add(
        AccountabilityOutcomeReceiptRecord(id=uuid4(), owner_id=owner_id, event_id=event_id)
    )
    if intent is None or intent.cycle_id != cycle_id or intent.review_id != review_id:
        await session.flush()
        return True
    newer = attempt_number > intent.last_outcome_attempt
    if newer:
        intent.last_outcome_attempt = attempt_number
        intent.failure_code = None if outcome == "confirmed" else intent.failure_code
        if intent.state != "confirmed":
            if outcome == "confirmed":
                intent.state = "confirmed"
            elif outcome == "failed":
                intent.state = "failed" if retrying else "exhausted"
            else:
                intent.state = "unknown" if outcome == "unknown" else "exhausted"
    if intent.cycle_id is not None and newer:
        if cycle is not None and cycle.state == CycleState.OPEN.value:
            if intent.slot == MessageSlot.INITIAL.value and outcome == "confirmed":
                confirmed_at = occurred_at.astimezone(UTC)
                if cycle.initial_confirmed_at is None or confirmed_at < cycle.initial_confirmed_at:
                    cycle.initial_confirmed_at = confirmed_at
                    cycle.nudge_due_at = confirmed_at + timedelta(hours=1)
            elif (
                intent.slot == MessageSlot.NUDGE.value
                and outcome
                in {
                    "confirmed",
                    "failed",
                    "unknown",
                    "blocked",
                }
                and not retrying
            ):
                cycle.state = CycleState.EXHAUSTED.value
    if intent.review_id is not None and newer:
        if review is not None and review.state == CycleState.OPEN.value:
            if intent.slot == MessageSlot.INITIAL.value and outcome == "confirmed":
                confirmed_at = occurred_at.astimezone(UTC)
                if (
                    review.initial_confirmed_at is None
                    or confirmed_at < review.initial_confirmed_at
                ):
                    review.initial_confirmed_at = confirmed_at
                    review.nudge_due_at = confirmed_at + timedelta(hours=1)
            elif (
                intent.slot == MessageSlot.NUDGE.value
                and outcome
                in {
                    "confirmed",
                    "failed",
                    "unknown",
                    "blocked",
                }
                and not retrying
            ):
                review.state = CycleState.EXHAUSTED.value
    await session.flush()
    return True


async def mark_delivery_intent_skipped(
    session: AsyncSession, *, intent_id: UUID, owner_id: UUID, reason: str
) -> bool:
    """Close an intent when owner/access/delivery policy makes sending impossible."""
    intent = await session.scalar(
        select(AccountabilityIntentRecord)
        .where(
            AccountabilityIntentRecord.id == intent_id,
            AccountabilityIntentRecord.owner_id == owner_id,
        )
        .with_for_update()
    )
    if intent is None or intent.state in {"confirmed", "unknown", "exhausted"}:
        return False
    intent.state = "skipped"
    intent.failure_code = reason[:80]
    await session.flush()
    return True


async def record_delivery_attempt_result(
    session: AsyncSession,
    *,
    intent_id: UUID,
    owner_id: UUID,
    outcome: str,
    retrying: bool,
    failure_code: str | None,
) -> None:
    """Persist Delivery's current attempt state; cycle timers still consume its event."""
    intent = await session.scalar(
        select(AccountabilityIntentRecord)
        .where(
            AccountabilityIntentRecord.id == intent_id,
            AccountabilityIntentRecord.owner_id == owner_id,
        )
        .with_for_update()
    )
    if intent is None:
        raise RuntimeError("Accountability delivery intent disappeared before result recording")
    intent.state = (
        "confirmed"
        if outcome == "confirmed"
        else "failed"
        if outcome == "failed" and retrying
        else "unknown"
        if outcome == "unknown"
        else "exhausted"
    )
    intent.failure_code = failure_code
    await session.flush()


async def _close_occurrence_cycles(
    session: AsyncSession, owner_id: UUID, occurrence_id: UUID, state: CycleState
) -> None:
    cycles = list(
        (
            await session.scalars(
                select(CheckInCycleRecord)
                .where(
                    CheckInCycleRecord.owner_id == owner_id,
                    CheckInCycleRecord.occurrence_id == occurrence_id,
                    CheckInCycleRecord.state == CycleState.OPEN.value,
                )
                .with_for_update()
            )
        ).all()
    )
    for cycle in cycles:
        cycle.state = state.value


async def _get_intent(
    session: AsyncSession,
    *,
    slot: MessageSlot,
    cycle_id: UUID | None = None,
    review_id: UUID | None = None,
) -> AccountabilityIntentRecord | None:
    subject = (
        AccountabilityIntentRecord.cycle_id == cycle_id
        if cycle_id is not None
        else AccountabilityIntentRecord.review_id == review_id
    )
    return cast(
        AccountabilityIntentRecord | None,
        await session.scalar(
            select(AccountabilityIntentRecord)
            .where(subject, AccountabilityIntentRecord.slot == slot.value)
            .with_for_update()
        ),
    )


async def _new_intent(
    session: AsyncSession,
    *,
    owner_id: UUID,
    slot: MessageSlot,
    due_at: datetime,
    cycle_id: UUID | None = None,
    review_id: UUID | None = None,
) -> AccountabilityIntentRecord:
    intent = AccountabilityIntentRecord(
        id=uuid4(),
        owner_id=owner_id,
        cycle_id=cycle_id,
        review_id=review_id,
        slot=slot.value,
        state="scheduled",
        due_at=due_at.astimezone(UTC),
        attempt_count=0,
    )
    session.add(intent)
    await session.flush()
    return intent


async def _enqueue_send(
    session: AsyncSession, intent: AccountabilityIntentRecord, owner_id: UUID
) -> None:
    await enqueue_job(
        session,
        lane=JobLane.DELIVERY,
        kind=DELIVERY_JOB_KIND,
        payload={"intent_id": str(intent.id)},
        deduplication_key=f"accountability:{intent.id}",
        owner_id=owner_id,
        max_attempts=8,
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("accountability clock must be timezone-aware")
