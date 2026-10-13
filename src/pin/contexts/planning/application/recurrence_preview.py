"""Typed preview and confirmed recurrence creation application service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from pin.contexts.billing.api import AccessUsageStore, CheckAccess, Operation
from pin.contexts.identity.api import IdentityStore, PreferencesSnapshot
from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceClock,
    RecurrenceCommandStore,
    RecurrenceConfirmationGrant,
    RecurrenceConfirmationProof,
    RecurrenceConfirmationVerifier,
)
from pin.contexts.planning.domain.plans import OffsetKind, ReminderOffset
from pin.contexts.planning.domain.recurrence import (
    RecurrenceProjection,
    RecurringPlanDraft,
    project_recurrence,
    recurrence_projection_digest,
)


class RecurrenceConfirmationRejected(ValueError):
    """The typed proof is absent, forged, cancelled, expired, or already consumed."""


class RecurrenceAccessDenied(ValueError):
    """Current owner access does not permit the requested series operation."""


class RecurrenceStalePreview(ValueError):
    """The profile or approved recurrence projection changed after preview."""


class RecurrenceIdempotencyConflict(ValueError):
    """The action key is already committed for another recurrence payload."""


@dataclass(frozen=True, slots=True)
class RecurrencePreview:
    owner_id: UUID
    preferences_version: int
    through: date
    projection: RecurrenceProjection
    approval_projection_sha256: str


@dataclass(frozen=True, slots=True)
class CreateConfirmedSeries:
    action_key: str
    expected_preferences_version: int
    draft: RecurringPlanDraft
    proof: RecurrenceConfirmationProof


def recurrence_defaults(preferences: PreferencesSnapshot) -> tuple[ReminderOffset, ...]:
    return tuple(
        offset
        for offset in (
            ReminderOffset(
                OffsetKind.CALENDAR_DAYS,
                preferences.reminder_calendar_days,
                "default",
            )
            if preferences.reminder_calendar_days
            else None,
            ReminderOffset(
                OffsetKind.ELAPSED_SECONDS,
                preferences.reminder_elapsed_hours * 3600,
                "default",
            )
            if preferences.reminder_elapsed_hours
            else None,
        )
        if offset is not None
    )


def preview_recurring_series(
    owner_id: UUID,
    draft: RecurringPlanDraft,
    *,
    expected_preferences_version: int,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    at: datetime,
    horizon_days: int = 90,
    max_occurrences: int = 32,
    anchor_date: date | None = None,
    anchor_sequence: int = 0,
    total_count: int | None = None,
) -> RecurrencePreview:
    if expected_preferences_version < 1:
        raise ValueError("expected_preferences_version must be positive")
    if not 1 <= horizon_days <= 366:
        raise ValueError("preview horizon must be between 1 and 366 days")
    current_horizon = at.astimezone(UTC).date() + timedelta(days=horizon_days)
    start_horizon = draft.start_date + timedelta(days=horizon_days)
    through = max(current_horizon, start_horizon)
    projection = project_recurrence(
        draft,
        owner_id=owner_id,
        default_zone=default_zone,
        default_reminders=default_reminders,
        at=at,
        through=through,
        max_occurrences=max_occurrences,
        anchor=anchor_date,
        first_sequence=anchor_sequence,
        anchor_sequence=anchor_sequence,
        total_count=total_count,
    )
    return RecurrencePreview(
        owner_id,
        expected_preferences_version,
        through,
        projection,
        approval_recurrence_digest(
            draft,
            expected_preferences_version=expected_preferences_version,
            default_zone=default_zone,
            default_reminders=default_reminders,
            projection=projection,
            through=through,
        ),
    )


async def create_confirmed_series(
    command: CreateConfirmedSeries,
    *,
    authenticated_owner_id: UUID,
    store: RecurrenceCommandStore,
    identity: IdentityStore,
    billing: AccessUsageStore,
    confirmation_verifier: RecurrenceConfirmationVerifier,
    clock: RecurrenceClock,
    preview_horizon_days: int = 90,
    max_occurrences: int = 32,
) -> dict[str, object]:
    now = _now(clock)
    if command.expected_preferences_version < 1:
        raise ValueError("expected_preferences_version must be positive")
    payload_sha = recurrence_payload_digest(
        command.draft,
        expected_preferences_version=command.expected_preferences_version,
    )
    await store.lock_action(authenticated_owner_id, command.action_key)
    receipt = await store.get_receipt(authenticated_owner_id, command.action_key)
    if receipt is not None:
        if receipt.get("payload_sha256") != payload_sha:
            raise RecurrenceIdempotencyConflict("action key was reused for another series")
        return receipt

    preferences = _check_preferences(
        await identity.get_preferences(authenticated_owner_id),
        authenticated_owner_id,
        command.expected_preferences_version,
    )
    access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, now)
    )
    if not access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit plan creation")
    preview = preview_recurring_series(
        authenticated_owner_id,
        command.draft,
        expected_preferences_version=command.expected_preferences_version,
        default_zone=preferences.home_zone,
        default_reminders=recurrence_defaults(preferences),
        at=now,
        horizon_days=preview_horizon_days,
        max_occurrences=max_occurrences,
    )
    grant = await confirmation_verifier.verify_and_consume(
        command.proof,
        owner_id=authenticated_owner_id,
        approved_projection_sha256=preview.approval_projection_sha256,
        at=now,
    )
    _require_grant(grant, command.proof, authenticated_owner_id, preview, now)

    current_preferences = _check_preferences(
        await identity.lock_preferences(authenticated_owner_id),
        authenticated_owner_id,
        command.expected_preferences_version,
    )
    write_at = _now(clock)
    _check_grant(grant, command.proof, authenticated_owner_id, preview, write_at)
    _check_preferences(
        current_preferences, authenticated_owner_id, command.expected_preferences_version
    )
    write_access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, write_at)
    )
    if not write_access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit plan creation")
    write_preview = preview_recurring_series(
        authenticated_owner_id,
        command.draft,
        expected_preferences_version=command.expected_preferences_version,
        default_zone=current_preferences.home_zone,
        default_reminders=recurrence_defaults(current_preferences),
        at=write_at,
        horizon_days=preview_horizon_days,
        max_occurrences=max_occurrences,
    )
    if write_preview.approval_projection_sha256 != preview.approval_projection_sha256:
        raise RecurrenceStalePreview(
            "recurrence dates or reminder projection changed after preview"
        )
    return await store.persist_series(
        owner_id=authenticated_owner_id,
        action_key=command.action_key,
        payload_sha256=payload_sha,
        draft=command.draft,
        projection=write_preview.projection,
        effective_reminders=recurrence_defaults(current_preferences),
        default_zone=current_preferences.home_zone,
        at=write_at,
        horizon_date=write_preview.through,
    )


def approval_recurrence_digest(
    draft: RecurringPlanDraft,
    *,
    expected_preferences_version: int,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    projection: RecurrenceProjection,
    through: date,
) -> str:
    body = {
        "schema_version": 1,
        "preferences_version": expected_preferences_version,
        "default_zone": default_zone,
        "default_reminders": [
            {"kind": reminder.kind.value, "amount": reminder.amount, "source": reminder.source}
            for reminder in default_reminders
        ],
        "through": through.isoformat(),
        "truncated": projection.truncated,
        "typed_projection_sha256": recurrence_projection_digest(
            draft, projection.occurrences
        ),
    }
    return _digest(body)


def recurrence_payload_digest(
    draft: RecurringPlanDraft, *, expected_preferences_version: int
) -> str:
    # The stable command key deliberately excludes time-dependent projection.
    return _digest(
        {
            "schema_version": 1,
            "preferences_version": expected_preferences_version,
            "series": recurrence_projection_digest(draft, ()),
        }
    )


def _check_preferences(
    preferences: PreferencesSnapshot | None, owner_id: UUID, expected_version: int
) -> PreferencesSnapshot:
    if preferences is None or preferences.owner_id != owner_id:
        raise RecurrenceAccessDenied("a current owner profile is required to create a series")
    if preferences.version != expected_version:
        raise RecurrenceStalePreview("preferences changed after recurrence preview")
    return preferences


def _require_grant(
    grant: RecurrenceConfirmationGrant | None,
    proof: RecurrenceConfirmationProof,
    owner_id: UUID,
    preview: RecurrencePreview,
    at: datetime,
) -> None:
    if grant is None:
        raise RecurrenceConfirmationRejected("confirmation is missing, cancelled, or consumed")
    _check_grant(grant, proof, owner_id, preview, at)


def _check_grant(
    grant: RecurrenceConfirmationGrant | None,
    proof: RecurrenceConfirmationProof,
    owner_id: UUID,
    preview: RecurrencePreview,
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
        or grant.expires_at.tzinfo is None
        or not grant.issued_at <= at < grant.expires_at
    ):
        raise RecurrenceConfirmationRejected("proof does not authorize the current projection")


def _now(clock: RecurrenceClock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _digest(value: dict[str, object]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
