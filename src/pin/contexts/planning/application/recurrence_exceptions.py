"""Approved recurrence exception projections and atomic application."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from uuid import UUID

from pin.contexts.billing.api import AccessUsageStore, CheckAccess, Operation
from pin.contexts.identity.api import IdentityStore
from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceClock,
    RecurrenceCommandStore,
    RecurrenceConfirmationGrant,
    RecurrenceConfirmationProof,
    RecurrenceConfirmationVerifier,
    SeriesSnapshot,
)
from pin.contexts.planning.application.recurrence_preview import (
    RecurrenceAccessDenied,
    RecurrenceConfirmationRejected,
    RecurrenceIdempotencyConflict,
    RecurrenceStalePreview,
)
from pin.contexts.planning.application.reminder_policy import apply_recurrence_quiet_policy
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    RecurrenceOccurrence,
    RecurrenceProjection,
    RecurrenceRule,
    RecurringPlanDraft,
    project_recurrence,
    recurrence_projection_digest,
    resolve_specific_occurrence,
)


@dataclass(frozen=True, slots=True)
class RecurrenceExceptionPreview:
    series_id: UUID
    series_version: int
    change: RecurrenceExceptionDraft
    updated_draft: RecurringPlanDraft
    projection: RecurrenceProjection
    through: date
    remaining_count: int | None
    approval_projection_sha256: str


@dataclass(frozen=True, slots=True)
class ConfirmedRecurrenceException:
    action_key: str
    series_id: UUID
    expected_series_version: int
    expected_preferences_version: int
    change: RecurrenceExceptionDraft
    proof: RecurrenceConfirmationProof


def preview_recurrence_exception(
    snapshot: SeriesSnapshot,
    change: RecurrenceExceptionDraft,
    *,
    owner_id: UUID,
    at: datetime,
    through: date,
    default_zone: str | None = None,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
    max_occurrences: int = 1000,
) -> RecurrenceExceptionPreview:
    if snapshot.state != "active":
        raise ValueError("a stopped recurrence series cannot be changed")
    target = next((item for item in snapshot.occurrences if item.sequence == change.sequence), None)
    if target is None:
        raise ValueError("the selected recurrence occurrence does not exist")
    if target.state != "open":
        raise ValueError("completed or cancelled history cannot be reopened")
    if change.replacement.kind is not snapshot.draft.kind:
        raise ValueError("a recurrence exception cannot change the plan kind")
    if change.replacement.title.strip() != snapshot.draft.title.strip():
        raise ValueError("a recurrence reschedule cannot change the plan title")

    old_rule = snapshot.draft.recurrence
    if change.scope is RecurrenceExceptionScope.SINGLE:
        if change.new_pattern is not None:
            raise ValueError("single occurrence changes cannot alter a recurrence pattern")
        updated_draft = snapshot.draft
        occurrence = resolve_specific_occurrence(
            updated_draft,
            change.replacement,
            owner_id=owner_id,
            sequence=change.sequence,
            default_zone=default_zone or snapshot.default_zone,
            default_reminders=snapshot.effective_reminders,
            now=at,
        )
        projection = _projection((occurrence,), updated_draft)
        remaining = None if snapshot.total_count is None else snapshot.total_count - change.sequence
    else:
        if any(
            item.sequence > change.sequence and item.state != "open"
            for item in snapshot.occurrences
        ):
            raise ValueError(
                "this-and-future rescheduling cannot rewrite completed or cancelled history"
            )
        updated_rule = change.new_pattern or old_rule
        if updated_rule.count != old_rule.count or updated_rule.end_date != old_rule.end_date:
            raise ValueError(
                "this-and-future exceptions preserve the original series stop condition"
            )
        updated_draft = RecurringPlanDraft(
            kind=snapshot.draft.kind,
            title=snapshot.draft.title,
            start_date=change.replacement.local_date,
            local_time=change.replacement.local_time,
            event_zone=change.replacement.event_zone or snapshot.draft.event_zone,
            recurrence=updated_rule,
            duration_seconds=change.replacement.duration_seconds,
            end_local_time=change.replacement.end_local_time,
            end_local_date=change.replacement.end_local_date,
            fold=change.replacement.fold,
            end_fold=change.replacement.end_fold,
            reminders=(
                change.replacement.reminders
                if change.replacement.reminders is not None
                else snapshot.draft.reminders
            ),
        )
        effective_reminders = (
            snapshot.effective_reminders
            if change.replacement.reminders is None
            else change.replacement.reminders
        )
        projection = project_recurrence(
            updated_draft,
            owner_id=owner_id,
            default_zone=default_zone or snapshot.default_zone,
            default_reminders=effective_reminders,
            at=at,
            through=through,
            max_occurrences=max_occurrences,
            anchor=change.replacement.local_date,
            first_sequence=change.sequence,
            anchor_sequence=change.sequence,
            total_count=snapshot.total_count,
        )
        if projection.truncated:
            raise ValueError("exception preview exceeds the bounded series horizon")
        remaining = None if snapshot.total_count is None else snapshot.total_count - change.sequence

    projection = apply_recurrence_quiet_policy(
        projection,
        now=at,
        quiet_zone=quiet_zone,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )

    digest = _exception_approval_digest(
        snapshot,
        change,
        updated_draft,
        projection,
        through,
        quiet_zone=quiet_zone,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )
    return RecurrenceExceptionPreview(
        snapshot.id,
        snapshot.version,
        change,
        updated_draft,
        projection,
        through,
        remaining,
        digest,
    )


async def apply_confirmed_recurrence_exception(
    command: ConfirmedRecurrenceException,
    *,
    authenticated_owner_id: UUID,
    store: RecurrenceCommandStore,
    identity: IdentityStore,
    billing: AccessUsageStore,
    confirmation_verifier: RecurrenceConfirmationVerifier,
    clock: RecurrenceClock,
) -> dict[str, object]:
    now = _now(clock)
    payload_sha = _exception_payload_digest(command)
    await store.lock_action(authenticated_owner_id, command.action_key)
    receipt = await store.get_receipt(authenticated_owner_id, command.action_key)
    if receipt is not None:
        if receipt.get("payload_sha256") != payload_sha:
            raise RecurrenceIdempotencyConflict("action key was reused for another exception")
        return receipt

    snapshot = await store.lock_series_snapshot(authenticated_owner_id, command.series_id)
    if snapshot is None:
        raise RecurrenceAccessDenied("series is missing or belongs to another owner")
    if snapshot.version != command.expected_series_version:
        raise RecurrenceStalePreview("series changed after exception preview")
    preferences = await identity.get_preferences(authenticated_owner_id)
    if preferences is None or preferences.owner_id != authenticated_owner_id:
        raise RecurrenceAccessDenied("a current owner profile is required to edit a series")
    if preferences.version != command.expected_preferences_version:
        raise RecurrenceStalePreview("preferences changed after exception preview")
    access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, now)
    )
    if not access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit plan changes")
    through = _exception_through(snapshot, command.change)
    preview = preview_recurrence_exception(
        snapshot,
        command.change,
        owner_id=authenticated_owner_id,
        at=now,
        through=through,
        default_zone=preferences.home_zone,
        quiet_zone=preferences.home_zone,
        quiet_start=preferences.quiet_start,
        quiet_end=preferences.quiet_end,
    )
    grant = await confirmation_verifier.verify_and_consume(
        command.proof,
        owner_id=authenticated_owner_id,
        approved_projection_sha256=preview.approval_projection_sha256,
        at=now,
    )
    _check_grant(grant, command.proof, authenticated_owner_id, preview, now)

    current_preferences = await identity.lock_preferences(authenticated_owner_id)
    write_at = _now(clock)
    _check_grant(grant, command.proof, authenticated_owner_id, preview, write_at)
    if (
        current_preferences is None
        or current_preferences.owner_id != authenticated_owner_id
        or current_preferences.version != command.expected_preferences_version
    ):
        raise RecurrenceStalePreview("preferences changed after exception preview")
    write_access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, write_at)
    )
    if not write_access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit plan changes")
    refreshed = preview_recurrence_exception(
        snapshot,
        command.change,
        owner_id=authenticated_owner_id,
        at=write_at,
        through=through,
        default_zone=current_preferences.home_zone,
        quiet_zone=current_preferences.home_zone,
        quiet_start=current_preferences.quiet_start,
        quiet_end=current_preferences.quiet_end,
    )
    if refreshed.approval_projection_sha256 != preview.approval_projection_sha256:
        raise RecurrenceStalePreview("the approved future schedule changed before application")
    return await store.apply_exception(
        owner_id=authenticated_owner_id,
        action_key=command.action_key,
        payload_sha256=payload_sha,
        snapshot=snapshot,
        change=command.change,
        projection=refreshed.projection,
        new_draft=refreshed.updated_draft,
        approved_preferences_version=command.expected_preferences_version,
        quiet_zone=current_preferences.home_zone,
        quiet_start=current_preferences.quiet_start,
        quiet_end=current_preferences.quiet_end,
        approval_projection_sha256=refreshed.approval_projection_sha256,
        at=write_at,
    )


def _projection(
    occurrences: tuple[RecurrenceOccurrence, ...], draft: RecurringPlanDraft
) -> RecurrenceProjection:
    dates = tuple(occurrence.schedule.local_date for occurrence in occurrences)
    return RecurrenceProjection(
        dates=dates,
        occurrences=occurrences,
        truncated=False,
        fingerprint=recurrence_projection_digest(draft, occurrences),
    )


def _exception_approval_digest(
    snapshot: SeriesSnapshot,
    change: RecurrenceExceptionDraft,
    draft: RecurringPlanDraft,
    projection: RecurrenceProjection,
    through: date,
    *,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
) -> str:
    replacement = change.replacement
    payload = {
        "schema_version": 1,
        "series_id": str(snapshot.id),
        "series_version": snapshot.version,
        "sequence": change.sequence,
        "scope": change.scope.value,
        "through": through.isoformat(),
        "quiet_policy": {
            "zone": quiet_zone,
            "start": quiet_start.isoformat(),
            "end": quiet_end.isoformat(),
        },
        "remaining_count": None
        if snapshot.total_count is None
        else snapshot.total_count - change.sequence,
        "replacement": {
            "kind": replacement.kind.value,
            "title": replacement.title,
            "local_date": replacement.local_date.isoformat(),
            "local_time": replacement.local_time.isoformat() if replacement.local_time else None,
            "event_zone": replacement.event_zone,
            "end_local_date": replacement.end_local_date.isoformat()
            if replacement.end_local_date
            else None,
            "end_local_time": replacement.end_local_time.isoformat()
            if replacement.end_local_time
            else None,
            "duration_seconds": replacement.duration_seconds,
            "fold": replacement.fold,
            "end_fold": replacement.end_fold,
            "reminders": None
            if replacement.reminders is None
            else [
                {"kind": rule.kind.value, "amount": rule.amount, "source": rule.source}
                for rule in replacement.reminders
            ],
        },
        "updated_pattern": _rule_payload(draft.recurrence),
        "projection_sha256": recurrence_projection_digest(draft, projection.occurrences),
        "truncated": projection.truncated,
    }
    return _digest(payload)


def _exception_payload_digest(command: ConfirmedRecurrenceException) -> str:
    change = command.change
    replacement = change.replacement
    return _digest(
        {
            "schema_version": 1,
            "series_id": str(command.series_id),
            "expected_series_version": command.expected_series_version,
            "expected_preferences_version": command.expected_preferences_version,
            "scope": change.scope.value,
            "sequence": change.sequence,
            "replacement": {
                "kind": replacement.kind.value,
                "title": replacement.title,
                "local_date": replacement.local_date.isoformat(),
                "local_time": replacement.local_time.isoformat()
                if replacement.local_time
                else None,
                "event_zone": replacement.event_zone,
                "duration_seconds": replacement.duration_seconds,
                "end_local_date": replacement.end_local_date.isoformat()
                if replacement.end_local_date
                else None,
                "end_local_time": replacement.end_local_time.isoformat()
                if replacement.end_local_time
                else None,
                "fold": replacement.fold,
                "end_fold": replacement.end_fold,
                "reminders": None
                if replacement.reminders is None
                else [
                    {
                        "kind": reminder.kind.value,
                        "amount": reminder.amount,
                        "source": reminder.source,
                    }
                    for reminder in replacement.reminders
                ],
            },
            "pattern": _rule_payload(change.new_pattern),
        }
    )


def _rule_payload(rule: RecurrenceRule | None) -> dict[str, object]:
    if rule is None:
        return {}
    return {
        "frequency": rule.frequency.value,
        "interval": rule.interval,
        "weekdays": sorted(rule.weekdays),
        "monthly_day": rule.monthly_day,
        "last_day": rule.last_day,
        "count": rule.count,
        "end_date": rule.end_date.isoformat() if rule.end_date else None,
    }


def _exception_through(snapshot: SeriesSnapshot, change: RecurrenceExceptionDraft) -> date:
    through = change.replacement.local_date + timedelta(days=366)
    stored = max((item.local_date for item in snapshot.occurrences), default=through)
    return max(through, stored)


def _check_grant(
    grant: RecurrenceConfirmationGrant | None,
    proof: RecurrenceConfirmationProof,
    owner_id: UUID,
    preview: RecurrenceExceptionPreview,
    at: datetime,
) -> None:
    if (
        grant is None
        or grant.owner_id != owner_id
        or grant.preview_id != proof.preview_id
        or grant.preview_version != proof.preview_version
        or not grant.producer_id.strip()
        or grant.approved_projection_sha256 != preview.approval_projection_sha256
        or grant.issued_at.tzinfo is None
        or grant.issued_at.utcoffset() is None
        or grant.expires_at.tzinfo is None
        or grant.expires_at.utcoffset() is None
        or not grant.issued_at <= at < grant.expires_at
    ):
        raise RecurrenceConfirmationRejected("proof does not authorize the current exception")


def _now(clock: RecurrenceClock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _digest(value: dict[str, object]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
