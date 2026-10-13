"""Preview and confirm version-fenced deterministic plan mutations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from pin.contexts.billing.api import AccountLifecycle, CheckAccess, Operation
from pin.contexts.planning.application.mutation_contracts import (
    MutationAccessStore,
    MutationClock,
    MutationConfirmationVerifier,
    MutationGrant,
    MutationIdentityStore,
    MutationProof,
    MutationStore,
)
from pin.contexts.planning.application.reminder_policy import apply_quiet_policy
from pin.contexts.planning.domain.mutations import (
    MutationAction,
    MutationCancellationProjection,
    MutationCancellationTarget,
    MutationPreview,
    MutationReminderProjection,
    MutationRequest,
    MutationScope,
    MutationSeriesProjection,
    MutationTarget,
    ensure_preview_current,
)
from pin.contexts.planning.domain.plans import (
    OneOffPlanDraft,
    ResolvedSchedule,
    resolve_reminder_projection,
    resolve_schedule,
)
from pin.contexts.planning.recurrence_api import (
    RecurrenceExceptionDraft,
    RecurrenceExceptionScope,
    SeriesSnapshot,
    preview_recurrence_exception,
)

MUTATION_PREVIEW_TTL = timedelta(hours=24)


class MutationRejected(ValueError):
    """A mutation is stale, unauthorized, expired, or otherwise inapplicable."""


class MutationStalePreview(MutationRejected):
    """The confirmed preview no longer describes current Planning state."""


class MutationAccessDenied(MutationRejected):
    """Current owner or entitlement policy denies this mutation."""


def _now(clock: MutationClock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


async def preview_mutation(
    request: MutationRequest,
    *,
    authenticated_owner_id: UUID,
    store: MutationStore,
    identity: MutationIdentityStore,
    billing: MutationAccessStore,
    clock: MutationClock,
) -> MutationPreview:
    """Persist an owner-bound preview; only `confirm_mutation` changes a plan."""
    now = _now(clock)
    if request.owner_id != authenticated_owner_id:
        raise MutationAccessDenied("mutation target is not owned by the authenticated user")
    target = await store.get_target(
        owner_id=authenticated_owner_id, occurrence_id=request.occurrence_id
    )
    if target is None or target.owner_id != authenticated_owner_id:
        raise MutationAccessDenied("occurrence is missing or belongs to another owner")
    if target.occurrence_version != request.expected_occurrence_version:
        raise MutationStalePreview("occurrence changed before its preview was created")
    if target.state.value != "open":
        raise MutationRejected("completed or cancelled occurrences cannot be changed")
    if (
        request.action is MutationAction.RESCHEDULE
        and target.schedule.anchor_at is not None
        and target.schedule.anchor_at <= now
    ):
        raise MutationRejected("an occurrence that has started cannot be rescheduled")
    if request.scope is MutationScope.THIS_AND_FUTURE and target.recurrence_series_id is None:
        raise MutationRejected("this-and-future applies only to recurring occurrences")

    preferences = await identity.get_preferences(authenticated_owner_id)
    if preferences is None or preferences.owner_id != authenticated_owner_id:
        raise MutationAccessDenied("a current owner profile is required")
    replacement = None
    series_projection = None
    cancellation_projection = None
    reminder_projection = None
    if request.action is MutationAction.CANCEL and request.scope is MutationScope.THIS_AND_FUTURE:
        assert target.recurrence_series_id is not None
        snapshot = await store.get_series_snapshot(
            owner_id=authenticated_owner_id,
            series_id=target.recurrence_series_id,
        )
        if snapshot is None or snapshot.owner_id != authenticated_owner_id:
            raise MutationAccessDenied("recurrence series is missing or belongs to another owner")
        cancellation_projection = _cancellation_projection(snapshot, target)
    if request.replacement is not None:
        if request.replacement.kind is not target.schedule.kind:
            raise MutationRejected("a reschedule cannot change the plan kind")
        effective_replacement = _effective_replacement(request.replacement, target)
        replacement = resolve_schedule(
            effective_replacement,
            default_zone=preferences.home_zone,
            now=now,
        )
        reminder_resolution = resolve_reminder_projection(
            replacement,
            effective_replacement.reminders or (),
            now=now,
        )
        reminder_resolution, notices = apply_quiet_policy(
            replacement,
            reminder_resolution,
            now=now,
            quiet_zone=preferences.home_zone,
            quiet_start=preferences.quiet_start,
            quiet_end=preferences.quiet_end,
        )
        reminder_projection = MutationReminderProjection(
            scheduled=reminder_resolution.scheduled,
            omitted=reminder_resolution.omitted,
            notices=notices,
        )
        if target.recurrence_series_id is not None:
            snapshot = await store.get_series_snapshot(
                owner_id=authenticated_owner_id,
                series_id=target.recurrence_series_id,
            )
            if snapshot is None or snapshot.owner_id != authenticated_owner_id:
                raise MutationAccessDenied(
                    "recurrence series is missing or belongs to another owner"
                )
            _ensure_not_started(target, snapshot, request, now)
            change = _series_change(request, target, effective_replacement)
            through = _exception_through(snapshot, effective_replacement.local_date)
            exception = preview_recurrence_exception(
                snapshot,
                change,
                owner_id=authenticated_owner_id,
                at=now,
                through=through,
                default_zone=preferences.home_zone,
                quiet_zone=preferences.home_zone,
                quiet_start=preferences.quiet_start,
                quiet_end=preferences.quiet_end,
            )
            series_projection = MutationSeriesProjection(
                series_version=snapshot.version,
                change=exception.change,
                updated_draft=exception.updated_draft,
                projection=exception.projection,
                through=exception.through,
                remaining_count=exception.remaining_count,
                approval_projection_sha256=exception.approval_projection_sha256,
            )
    projection = _projection_digest(
        request,
        target,
        replacement,
        preferences.version,
        reminder_projection=reminder_projection,
        series_projection=series_projection,
        cancellation_projection=cancellation_projection,
        quiet_zone=preferences.home_zone,
        quiet_start=preferences.quiet_start,
        quiet_end=preferences.quiet_end,
    )
    if await billing.account_lifecycle(authenticated_owner_id) is not AccountLifecycle.PRESENT:
        raise MutationAccessDenied("the current owner account is unavailable")
    access = await billing.lock_access(
        CheckAccess(authenticated_owner_id, _billing_operation(request.action), now)
    )
    if not access.allowed:
        raise MutationAccessDenied("current billing access does not permit this change")
    preview = MutationPreview(
        preview_id=uuid4(),
        owner_id=authenticated_owner_id,
        target=target,
        request=request,
        replacement_schedule=replacement,
        series_projection=series_projection,
        cancellation_projection=cancellation_projection,
        reminder_projection=reminder_projection,
        series_preview_schedules=(
            tuple(item.schedule for item in series_projection.projection.occurrences)
            if series_projection is not None
            else ()
        ),
        expected_preferences_version=preferences.version,
        preview_version=1,
        created_at=now,
        expires_at=now + MUTATION_PREVIEW_TTL,
        approval_projection_sha256=projection,
    )
    await store.save_preview(preview)
    return preview


async def confirm_mutation(
    proof: MutationProof,
    *,
    authenticated_owner_id: UUID,
    store: MutationStore,
    identity: MutationIdentityStore,
    billing: MutationAccessStore,
    confirmation_verifier: MutationConfirmationVerifier,
    clock: MutationClock,
) -> dict[str, object]:
    """Recheck identity, Planning version, entitlement and proof before one UoW write."""
    now = _now(clock)
    preferences = await identity.lock_preferences(authenticated_owner_id)
    if preferences is None or preferences.owner_id != authenticated_owner_id:
        raise MutationAccessDenied("a current owner profile is required")
    existing_receipt = await store.get_receipt(
        owner_id=authenticated_owner_id, preview_id=proof.preview_id
    )
    if existing_receipt is not None:
        if existing_receipt.get("preview_id") != str(proof.preview_id):
            raise MutationStalePreview("mutation receipt does not match this preview")
        return existing_receipt
    unlocked = await store.get_preview(owner_id=authenticated_owner_id, preview_id=proof.preview_id)
    if unlocked is None:
        raise MutationAccessDenied("mutation preview is missing or belongs to another owner")
    series_snapshot = None
    if (
        unlocked.request.action is MutationAction.RESCHEDULE
        or (
            unlocked.request.action is MutationAction.CANCEL
            and unlocked.request.scope is MutationScope.THIS_AND_FUTURE
        )
    ) and unlocked.target.recurrence_series_id is not None:
        series_snapshot = await store.get_series_snapshot(
            owner_id=authenticated_owner_id,
            series_id=unlocked.target.recurrence_series_id,
            lock=True,
        )
    preview = await store.get_preview(
        owner_id=authenticated_owner_id, preview_id=proof.preview_id, lock=True
    )
    if preview is None:
        raise MutationAccessDenied("mutation preview is missing or belongs to another owner")
    if (
        preview.preview_version != unlocked.preview_version
        or preview.approval_projection_sha256 != unlocked.approval_projection_sha256
    ):
        raise MutationStalePreview("mutation preview changed while it was being confirmed")
    if proof.preview_version != preview.preview_version:
        raise MutationStalePreview("mutation preview changed")
    if preferences.version != preview.expected_preferences_version:
        raise MutationStalePreview("preferences changed after preview")
    target = await store.get_target(
        owner_id=authenticated_owner_id,
        occurrence_id=preview.target.occurrence_id,
        lock=True,
    )
    if target is None:
        raise MutationAccessDenied("occurrence is missing or belongs to another owner")
    try:
        ensure_preview_current(preview, target, owner_id=authenticated_owner_id, at=now)
    except ValueError as exc:
        raise MutationStalePreview(str(exc)) from exc
    if preview.cancellation_projection is not None:
        if series_snapshot is None:
            raise MutationStalePreview("recurrence series changed after cancellation preview")
        current_cancellation = _cancellation_projection(series_snapshot, target)
        if current_cancellation != preview.cancellation_projection:
            raise MutationStalePreview("future occurrences changed after cancellation preview")
    if (
        preview.request.action is MutationAction.RESCHEDULE
        and target.schedule.anchor_at is not None
        and target.schedule.anchor_at <= now
    ):
        raise MutationStalePreview("an occurrence that has started cannot be rescheduled")
    replacement = None
    series_projection = None
    reminder_projection = None
    if preview.request.replacement is not None:
        effective_replacement = _effective_replacement(preview.request.replacement, target)
        replacement = resolve_schedule(
            effective_replacement,
            default_zone=preferences.home_zone,
            now=now,
        )
        reminder_resolution = resolve_reminder_projection(
            replacement,
            effective_replacement.reminders or (),
            now=now,
        )
        reminder_resolution, notices = apply_quiet_policy(
            replacement,
            reminder_resolution,
            now=now,
            quiet_zone=preferences.home_zone,
            quiet_start=preferences.quiet_start,
            quiet_end=preferences.quiet_end,
        )
        reminder_projection = MutationReminderProjection(
            scheduled=reminder_resolution.scheduled,
            omitted=reminder_resolution.omitted,
            notices=notices,
        )
        if target.recurrence_series_id is not None:
            if series_snapshot is None or series_snapshot.version < 1:
                raise MutationStalePreview("recurrence series changed after preview")
            _ensure_not_started(target, series_snapshot, preview.request, now)
            change = _series_change(preview.request, target, effective_replacement)
            exception = preview_recurrence_exception(
                series_snapshot,
                change,
                owner_id=authenticated_owner_id,
                at=now,
                through=_exception_through(series_snapshot, effective_replacement.local_date),
                default_zone=preferences.home_zone,
                quiet_zone=preferences.home_zone,
                quiet_start=preferences.quiet_start,
                quiet_end=preferences.quiet_end,
            )
            series_projection = MutationSeriesProjection(
                series_version=series_snapshot.version,
                change=exception.change,
                updated_draft=exception.updated_draft,
                projection=exception.projection,
                through=exception.through,
                remaining_count=exception.remaining_count,
                approval_projection_sha256=exception.approval_projection_sha256,
            )
    preview = replace(
        preview,
        series_projection=series_projection,
        cancellation_projection=preview.cancellation_projection,
        reminder_projection=reminder_projection,
        series_preview_schedules=(
            tuple(item.schedule for item in series_projection.projection.occurrences)
            if series_projection is not None
            else ()
        ),
    )
    current_projection = _projection_digest(
        preview.request,
        target,
        replacement,
        preferences.version,
        reminder_projection=reminder_projection,
        series_projection=series_projection,
        cancellation_projection=preview.cancellation_projection,
        quiet_zone=preferences.home_zone,
        quiet_start=preferences.quiet_start,
        quiet_end=preferences.quiet_end,
    )
    if current_projection != preview.approval_projection_sha256:
        raise MutationStalePreview("the approved schedule or preferences changed")
    lifecycle = await billing.lock_account_lifecycle(authenticated_owner_id)
    if lifecycle is not AccountLifecycle.PRESENT:
        raise MutationAccessDenied("the current owner account is unavailable")
    access = await billing.lock_access(
        CheckAccess(
            authenticated_owner_id,
            _billing_operation(preview.request.action),
            now,
        )
    )
    if not access.allowed:
        raise MutationAccessDenied("current billing access does not permit this change")
    grant = await confirmation_verifier.verify_and_consume(
        proof,
        owner_id=authenticated_owner_id,
        projection_sha256=preview.approval_projection_sha256,
        at=now,
    )
    _check_grant(grant, proof, authenticated_owner_id, preview, now)
    return await store.apply(preview, preferences=preferences, at=now)


def _billing_operation(action: MutationAction) -> Operation:
    return {
        MutationAction.COMPLETE: Operation.COMPLETE_PLAN,
        MutationAction.CANCEL: Operation.CANCEL_PLAN,
        MutationAction.RESCHEDULE: Operation.RESCHEDULE_PLAN,
    }[action]


def _effective_replacement(requested: OneOffPlanDraft, target: MutationTarget) -> OneOffPlanDraft:
    """Keep stored zone/reminders when a deterministic edit omits those fields."""
    return replace(
        requested,
        event_zone=requested.event_zone or target.schedule.event_zone,
        reminders=requested.reminders
        if requested.reminders is not None
        else target.reminder_offsets,
    )


def _series_change(
    request: MutationRequest, target: MutationTarget, replacement: OneOffPlanDraft
) -> RecurrenceExceptionDraft:
    scope = (
        RecurrenceExceptionScope.THIS_AND_FUTURE
        if request.scope is MutationScope.THIS_AND_FUTURE
        else RecurrenceExceptionScope.SINGLE
    )
    assert target.recurrence_sequence is not None
    return RecurrenceExceptionDraft(scope, target.recurrence_sequence, replacement)


def _cancellation_projection(
    snapshot: SeriesSnapshot, target: MutationTarget
) -> MutationCancellationProjection:
    if target.recurrence_sequence is None:
        raise MutationRejected("this-and-future cancellation requires a recurring occurrence")
    selected = tuple(
        MutationCancellationTarget(
            occurrence_id=item.occurrence_id,
            sequence=item.sequence,
            recurrence_key=item.recurrence_key,
            version=item.version,
            local_date=item.local_date,
        )
        for item in snapshot.occurrences
        if item.sequence >= target.recurrence_sequence and item.state == "open"
    )
    if not selected or selected[0].occurrence_id != target.occurrence_id:
        raise MutationStalePreview("the target is not an open future occurrence")
    projection = {
        "schema_version": 1,
        "operation": "cancel_this_and_future",
        "owner_id": str(snapshot.owner_id),
        "series_id": str(snapshot.id),
        "series_version": snapshot.version,
        "targets": [
            {
                "occurrence_id": str(item.occurrence_id),
                "sequence": item.sequence,
                "recurrence_key": item.recurrence_key,
                "version": item.version,
                "local_date": item.local_date.isoformat(),
            }
            for item in selected
        ],
    }
    return MutationCancellationProjection(
        series_version=snapshot.version,
        targets=selected,
        approval_projection_sha256=hashlib.sha256(
            json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def _exception_through(snapshot: SeriesSnapshot, start: date) -> date:
    stored = max((item.local_date for item in snapshot.occurrences), default=start)
    return max(start + timedelta(days=366), stored)


def _ensure_not_started(
    target: MutationTarget, snapshot: SeriesSnapshot, request: MutationRequest, now: datetime
) -> None:
    first_sequence = target.recurrence_sequence
    if first_sequence is None:
        return
    selected = request.scope is MutationScope.THIS_AND_FUTURE
    for occurrence in snapshot.occurrences:
        in_scope = (
            occurrence.sequence >= first_sequence
            if selected
            else occurrence.sequence == first_sequence
        )
        if (
            in_scope
            and occurrence.state == "open"
            and occurrence.anchor_at is not None
            and occurrence.anchor_at <= now
        ):
            raise MutationRejected(
                "a started occurrence in the selected scope cannot be rescheduled"
            )


def _projection_digest(
    request: MutationRequest,
    target: MutationTarget,
    replacement: ResolvedSchedule | None,
    preferences_version: int,
    *,
    reminder_projection: MutationReminderProjection | None = None,
    series_projection: MutationSeriesProjection | None = None,
    cancellation_projection: MutationCancellationProjection | None = None,
    quiet_zone: str | None = None,
    quiet_start: time | None = None,
    quiet_end: time | None = None,
) -> str:
    schedule = replacement
    if schedule is not None and request.replacement is None:
        raise MutationRejected("replacement schedule requires a complete replacement draft")
    payload = {
        "schema_version": 1,
        "owner_id": str(request.owner_id),
        "occurrence_id": str(request.occurrence_id),
        "plan_item_id": str(target.plan_item_id),
        "title": target.title,
        "occurrence_version": target.occurrence_version,
        "plan_version": target.plan_version,
        "preferences_version": preferences_version,
        "approved_preferences_version": target.approved_preferences_version,
        "quiet_policy": {
            "zone": quiet_zone,
            "start": quiet_start.isoformat() if quiet_start else None,
            "end": quiet_end.isoformat() if quiet_end else None,
        },
        "action": request.action.value,
        "scope": request.scope.value,
        "replacement": None
        if schedule is None
        else {
            "kind": schedule.kind.value,
            "title": request.replacement.title if request.replacement is not None else None,
            "event_zone": schedule.event_zone,
            "local_date": schedule.local_date.isoformat(),
            "local_time": schedule.local_time.isoformat() if schedule.local_time else None,
            "starts_at": schedule.starts_at.isoformat() if schedule.starts_at else None,
            "ends_at": schedule.ends_at.isoformat() if schedule.ends_at else None,
            "deadline_at": schedule.deadline_at.isoformat() if schedule.deadline_at else None,
            "reminders": [
                {
                    "kind": item.offset.kind.value,
                    "amount": item.offset.amount,
                    "source": item.offset.source,
                    "due_at": item.due_at.astimezone(UTC).isoformat(),
                }
                for item in reminder_projection.scheduled
            ]
            if reminder_projection
            else [],
            "omitted_reminders": [
                {
                    "kind": item.offset.kind.value,
                    "amount": item.offset.amount,
                    "source": item.offset.source,
                    "due_at": item.due_at.astimezone(UTC).isoformat(),
                    "reason": item.reason,
                }
                for item in reminder_projection.omitted
            ]
            if reminder_projection
            else [],
            "reminder_notices": [
                {
                    "kind": item.kind,
                    "kind_offset": item.offset.kind.value,
                    "amount": item.offset.amount,
                    "source": item.offset.source,
                    "from": item.original_due_at.astimezone(UTC).isoformat(),
                    "to": item.due_at.astimezone(UTC).isoformat(),
                }
                for item in reminder_projection.notices
            ]
            if reminder_projection
            else [],
        },
        "series_projection_sha256": (
            series_projection.approval_projection_sha256 if series_projection else None
        ),
        "cancellation_projection_sha256": (
            cancellation_projection.approval_projection_sha256 if cancellation_projection else None
        ),
        "cancellation_targets": [
            {
                "occurrence_id": str(item.occurrence_id),
                "sequence": item.sequence,
                "recurrence_key": item.recurrence_key,
                "version": item.version,
                "local_date": item.local_date.isoformat(),
            }
            for item in cancellation_projection.targets
        ]
        if cancellation_projection
        else [],
        "cancellation_series_version": (
            cancellation_projection.series_version if cancellation_projection else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _check_grant(
    grant: MutationGrant | None,
    proof: MutationProof,
    owner_id: UUID,
    preview: MutationPreview,
    at: datetime,
) -> None:
    if (
        grant is None
        or grant.owner_id != owner_id
        or grant.preview_id != preview.preview_id
        or grant.preview_version != preview.preview_version
        or grant.projection_sha256 != preview.approval_projection_sha256
        or grant.expires_at <= at
        or not proof.token
    ):
        raise MutationRejected("mutation confirmation is invalid, stale, or expired")


__all__ = [
    "MUTATION_PREVIEW_TTL",
    "MutationAccessDenied",
    "MutationRejected",
    "MutationStalePreview",
    "confirm_mutation",
    "preview_mutation",
]
