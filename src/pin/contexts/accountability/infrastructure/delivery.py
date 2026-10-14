"""Live-truth authorization for Accountability sends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.accountability.application.contracts import (
    AccountabilitySend,
    CurrentOccurrenceReader,
    ReviewProfile,
    TodayUntimedReader,
)
from pin.contexts.accountability.domain.cycles import CycleState, MessageSlot
from pin.contexts.accountability.infrastructure.models import (
    AccountabilityIntentRecord,
    CheckInCycleRecord,
    DailyReviewRecord,
)
from pin.contexts.notifications.api import defer_until_quiet_hours_end


@dataclass(frozen=True, slots=True)
class DeliveryAuthorization:
    send: AccountabilitySend | None
    deferred_until: datetime | None = None
    reason: str | None = None


async def authorize_accountability_send(
    session: AsyncSession,
    *,
    intent_id: UUID,
    profile: ReviewProfile,
    current_occurrences: CurrentOccurrenceReader,
    today_untimed: TodayUntimedReader,
    now: datetime,
    expected_dispatch_version: int | None = None,
) -> DeliveryAuthorization:
    """Fence a send against current owner, occurrence, date, quiet and cycle state."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("delivery clock must be timezone-aware")
    if expected_dispatch_version is not None and (
        type(expected_dispatch_version) is not int or expected_dispatch_version < 1
    ):
        raise ValueError("expected dispatch version must be a positive integer")
    candidate = await session.scalar(
        select(AccountabilityIntentRecord).where(
            AccountabilityIntentRecord.id == intent_id,
            AccountabilityIntentRecord.owner_id == profile.owner_id,
        )
    )
    if candidate is None:
        return DeliveryAuthorization(None, reason="intent_not_sendable")
    locked_cycle: CheckInCycleRecord | None = None
    locked_review: DailyReviewRecord | None = None
    if candidate.cycle_id is not None:
        locked_cycle = await session.scalar(
            select(CheckInCycleRecord)
            .where(
                CheckInCycleRecord.id == candidate.cycle_id,
                CheckInCycleRecord.owner_id == profile.owner_id,
            )
            .with_for_update()
        )
    elif candidate.review_id is not None:
        locked_review = await session.scalar(
            select(DailyReviewRecord)
            .where(
                DailyReviewRecord.id == candidate.review_id,
                DailyReviewRecord.owner_id == profile.owner_id,
            )
            .with_for_update()
        )
    intent = await session.scalar(
        select(AccountabilityIntentRecord)
        .where(
            AccountabilityIntentRecord.id == intent_id,
            AccountabilityIntentRecord.owner_id == profile.owner_id,
        )
        .with_for_update()
    )
    if (
        intent is None
        or intent.state not in {"scheduled", "failed"}
        or intent.dispatch_version
        != (expected_dispatch_version if expected_dispatch_version is not None else 1)
        or intent.cycle_id != candidate.cycle_id
        or intent.review_id != candidate.review_id
    ):
        return DeliveryAuthorization(None, reason="intent_not_sendable")
    due_at = max(
        intent.due_at.astimezone(UTC),
        intent.not_before_at.astimezone(UTC)
        if intent.not_before_at
        else intent.due_at.astimezone(UTC),
    )
    at = now.astimezone(UTC)
    if due_at > at:
        return DeliveryAuthorization(None, deferred_until=due_at, reason="not_due")
    quiet_retry = defer_until_quiet_hours_end(
        now,
        zone_name=profile.home_zone,
        quiet_start=profile.quiet_start,
        quiet_end=profile.quiet_end,
    )
    if quiet_retry > at:
        intent.not_before_at = quiet_retry
        intent.state = "scheduled"
        return DeliveryAuthorization(None, deferred_until=quiet_retry, reason="quiet_hours")

    slot = MessageSlot(intent.slot)
    if intent.cycle_id is not None:
        cycle = locked_cycle
        if cycle is None or cycle.state != CycleState.OPEN.value:
            intent.state = "skipped"
            intent.retryable_after_access = False
            return DeliveryAuthorization(None, reason="cycle_closed")
        current = await current_occurrences.get_current_occurrence(
            profile.owner_id, cycle.occurrence_id, for_update=True
        )
        if (
            current is None
            or current.owner_id != profile.owner_id
            or current.state != "open"
            or current.occurrence_version != cycle.occurrence_version
            or current.kind != cycle.kind
        ):
            intent.state = "skipped"
            intent.retryable_after_access = False
            if current is not None and current.state == "completed":
                cycle.state = CycleState.COMPLETED.value
            elif current is not None and current.state == "cancelled":
                cycle.state = CycleState.CANCELLED.value
            else:
                cycle.state = CycleState.SUPERSEDED.value
            return DeliveryAuthorization(None, reason="planning_truth_changed")
        if slot is MessageSlot.INITIAL:
            if current.kind == "appointment" and current.ends_at is None:
                intent.state = "skipped"
                intent.retryable_after_access = False
                return DeliveryAuthorization(None, reason="appointment_end_missing")
            expected_end = current.ends_at if current.kind == "appointment" else current.deadline_at
            if expected_end is None or expected_end.astimezone(UTC) > at:
                return DeliveryAuthorization(
                    None, deferred_until=expected_end, reason="check_not_due"
                )
        elif slot is MessageSlot.NUDGE:
            if cycle.initial_confirmed_at is None or cycle.nudge_due_at is None:
                intent.state = "skipped"
                intent.retryable_after_access = False
                return DeliveryAuthorization(None, reason="initial_send_not_confirmed")
            if cycle.nudge_due_at.astimezone(UTC) > at:
                return DeliveryAuthorization(
                    None,
                    deferred_until=cycle.nudge_due_at.astimezone(UTC),
                    reason="nudge_not_due",
                )
        text = _checkin_text(current.title, profile.language, slot)
        occurrence_ids = (current.occurrence_id,)
        send = AccountabilitySend(
            intent.id,
            profile.owner_id,
            slot,
            profile.destination_id,
            profile.language,
            current.title,
            occurrence_ids,
            cycle.id,
            None,
            text,
            intent.dispatch_version,
        )
    elif intent.review_id is not None:
        review = locked_review
        if review is None or review.state != CycleState.OPEN.value:
            intent.state = "skipped"
            intent.retryable_after_access = False
            return DeliveryAuthorization(None, reason="review_closed")
        today = at.astimezone(ZoneInfo(profile.home_zone)).date()
        if review.review_date != today:
            review.state = CycleState.SUPERSEDED.value
            intent.state = "skipped"
            intent.retryable_after_access = False
            return DeliveryAuthorization(None, reason="review_day_expired")
        if slot is MessageSlot.NUDGE:
            if review.initial_confirmed_at is None or review.nudge_due_at is None:
                intent.state = "skipped"
                intent.retryable_after_access = False
                return DeliveryAuthorization(None, reason="initial_send_not_confirmed")
            if review.nudge_due_at.astimezone(UTC) > at:
                return DeliveryAuthorization(
                    None,
                    deferred_until=review.nudge_due_at.astimezone(UTC),
                    reason="nudge_not_due",
                )
        today_items = await today_untimed.list_today_open_untimed(
            profile.owner_id, profile_zone=profile.home_zone, at=now, for_update=True
        )
        current_by_id = {
            item.occurrence_id: item for item in today_items if item.local_date == today
        }
        ids = tuple(UUID(value) for value in review.occurrence_ids if UUID(value) in current_by_id)
        if not ids:
            review.state = CycleState.SUPERSEDED.value
            intent.state = "skipped"
            intent.retryable_after_access = False
            return DeliveryAuthorization(None, reason="review_empty")
        titles = tuple(current_by_id[item].title for item in ids)
        text = _review_text(profile.language, slot, titles)
        send = AccountabilitySend(
            intent.id,
            profile.owner_id,
            slot,
            profile.destination_id,
            profile.language,
            None,
            ids,
            None,
            review.id,
            text,
            intent.dispatch_version,
        )
    else:
        intent.state = "skipped"
        intent.retryable_after_access = False
        return DeliveryAuthorization(None, reason="intent_subject_missing")

    intent.state = "sending"
    intent.retryable_after_access = False
    intent.attempt_count += 1
    return DeliveryAuthorization(send)


def _checkin_text(title: str, language: str, slot: MessageSlot) -> str:
    if language == "ru":
        prefix = "Удалось завершить" if slot is MessageSlot.NUDGE else "Вы завершили"
        return f"{prefix} «{title}»?"
    if language == "en":
        prefix = "Did you manage to finish" if slot is MessageSlot.NUDGE else "Did you complete"
        return f"{prefix} “{title}”?"
    raise ValueError("accountability language must be en or ru")


def _review_text(language: str, slot: MessageSlot, titles: tuple[str, ...]) -> str:
    followup = slot is MessageSlot.NUDGE
    if language == "ru":
        heading = "Удалось что-нибудь завершить?" if followup else "Что из этого удалось сделать?"
        lines = [heading, *(f"• {title}" for title in titles[:10])]
        if len(titles) > 10:
            lines.append(f"И ещё {len(titles) - 10}.")
    elif language == "en":
        heading = (
            "Did you manage to finish any of these?"
            if followup
            else "Which of these did you complete?"
        )
        lines = [heading, *(f"• {title}" for title in titles[:10])]
        if len(titles) > 10:
            lines.append(f"And {len(titles) - 10} more.")
    else:
        raise ValueError("accountability language must be en or ru")
    return "\n".join(lines)[:3500]
