"""Confirmed typed commands for one-off plans and atomic batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pin.contexts.billing.api import AccessUsageStore, CheckAccess, Operation
from pin.contexts.identity.api import IdentityStore
from pin.contexts.planning.application.contracts import (
    Clock,
    ConfirmationGrant,
    ConfirmationProof,
    ConfirmationVerifier,
    PlanCommandStore,
)
from pin.contexts.planning.application.preview import preview_plan_batch
from pin.contexts.planning.domain.plans import (
    OffsetKind,
    OneOffPlanDraft,
    ReminderOffset,
    payload_digest,
)


class ConfirmationRejected(ValueError):
    """The proof did not authorize this exact owner, preview, and payload."""


class AccessDenied(ValueError):
    """Billing denied the current owner the requested plan operation."""


class StalePreview(ValueError):
    """The profile or target changed after the user approved the preview."""


class IdempotencyConflict(ValueError):
    """The owner reused an action key for a different typed payload."""


@dataclass(frozen=True, slots=True)
class ConfirmedPlanBatch:
    action_key: str
    expected_preferences_version: int
    plans: tuple[OneOffPlanDraft, ...]
    proof: ConfirmationProof


async def create_confirmed_plan_batch(
    command: ConfirmedPlanBatch,
    *,
    authenticated_owner_id: UUID,
    store: PlanCommandStore,
    identity: IdentityStore,
    billing: AccessUsageStore,
    confirmation_verifier: ConfirmationVerifier,
    clock: Clock,
) -> dict[str, object]:
    """Persist a user-confirmed typed command; caller owns the shared UoW."""
    now = clock.now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    now = now.astimezone(UTC)
    if not command.plans:
        raise ValueError("a confirmed plan batch cannot be empty")
    if command.expected_preferences_version < 1:
        raise ValueError("expected_preferences_version must be positive")
    digest = payload_digest(
        command.plans, expected_preferences_version=command.expected_preferences_version
    )

    # The advisory lock is the first database operation; replay returns only the
    # original committed result and never creates a second event or plan.
    await store.lock_action(authenticated_owner_id, command.action_key)
    receipt = await store.get_receipt(authenticated_owner_id, command.action_key)
    if receipt is not None:
        if receipt.get("payload_sha256") != digest:
            raise IdempotencyConflict("action_key was reused with a different plan payload")
        return receipt

    preferences = await identity.get_preferences(authenticated_owner_id)
    if preferences is None or preferences.owner_id != authenticated_owner_id:
        raise AccessDenied("a current owner profile is required to create plans")
    if preferences.version != command.expected_preferences_version:
        raise StalePreview("preferences changed after the plan preview")
    access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, now)
    )
    if not access.allowed:
        raise AccessDenied("current billing access does not permit plan creation")

    default_offsets = tuple(
        offset
        for offset in (
            ReminderOffset(OffsetKind.CALENDAR_DAYS, preferences.reminder_calendar_days, "default")
            if preferences.reminder_calendar_days
            else None,
            ReminderOffset(
                OffsetKind.ELAPSED_SECONDS, preferences.reminder_elapsed_hours * 3600, "default"
            )
            if preferences.reminder_elapsed_hours
            else None,
        )
        if offset is not None
    )
    preview = preview_plan_batch(
        authenticated_owner_id,
        command.plans,
        expected_preferences_version=command.expected_preferences_version,
        default_zone=preferences.home_zone,
        default_reminders=default_offsets,
        quiet_zone=preferences.home_zone,
        quiet_start=preferences.quiet_start,
        quiet_end=preferences.quiet_end,
        at=now,
    )

    grant = await confirmation_verifier.verify_and_consume(
        command.proof,
        owner_id=authenticated_owner_id,
        approved_projection_sha256=preview.approval_projection_sha256,
        at=now,
    )
    _require_confirmation(
        grant, command.proof, authenticated_owner_id, preview.approval_projection_sha256, now
    )

    # Proof verification may await its trusted producer, and the preference read
    # above may predate that wait. Lock and refresh the public Identity snapshot
    # before taking the final clock/access snapshot so authority cannot go stale
    # while the command is being applied.
    current_preferences = await identity.lock_preferences(authenticated_owner_id)
    write_at = clock.now()
    if write_at.tzinfo is None or write_at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    write_at = write_at.astimezone(UTC)
    _require_confirmation(
        grant,
        command.proof,
        authenticated_owner_id,
        preview.approval_projection_sha256,
        write_at,
    )
    if current_preferences is None or current_preferences.owner_id != authenticated_owner_id:
        raise AccessDenied("a current owner profile is required to create plans")
    if current_preferences.version != command.expected_preferences_version:
        raise StalePreview("preferences changed after the plan preview")
    current_access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CREATE_PLAN, write_at)
    )
    if not current_access.allowed:
        raise AccessDenied("current billing access does not permit plan creation")

    # Re-resolve against the write-boundary clock and locked current preferences.
    # A changed event or effective/omitted reminder means the approved preview is
    # stale and must be shown again before any Planning fragments are persisted.
    write_offsets = tuple(
        offset
        for offset in (
            ReminderOffset(
                OffsetKind.CALENDAR_DAYS,
                current_preferences.reminder_calendar_days,
                "default",
            )
            if current_preferences.reminder_calendar_days
            else None,
            ReminderOffset(
                OffsetKind.ELAPSED_SECONDS,
                current_preferences.reminder_elapsed_hours * 3600,
                "default",
            )
            if current_preferences.reminder_elapsed_hours
            else None,
        )
        if offset is not None
    )
    write_preview = preview_plan_batch(
        authenticated_owner_id,
        command.plans,
        expected_preferences_version=command.expected_preferences_version,
        default_zone=current_preferences.home_zone,
        default_reminders=write_offsets,
        quiet_zone=current_preferences.home_zone,
        quiet_start=current_preferences.quiet_start,
        quiet_end=current_preferences.quiet_end,
        at=write_at,
    )
    if write_preview.approval_projection_sha256 != preview.approval_projection_sha256:
        raise StalePreview("the resolved schedule changed after the user approved the preview")
    return await store.persist_confirmed_batch(
        owner_id=authenticated_owner_id,
        action_key=command.action_key,
        payload_sha256=digest,
        approved_preferences_version=command.expected_preferences_version,
        plans=write_preview.plans,
        at=write_at,
    )


def _require_confirmation(
    grant: ConfirmationGrant | None,
    proof: ConfirmationProof,
    owner_id: UUID,
    approved_projection_sha256: str,
    at: datetime,
) -> None:
    if grant is None:
        raise ConfirmationRejected(
            "confirmation is missing, cancelled, expired, forged, or consumed"
        )
    if (
        grant.owner_id != owner_id
        or grant.preview_id != proof.preview_id
        or grant.preview_version != proof.preview_version
        or not grant.producer_id.strip()
        or grant.approved_projection_sha256 != approved_projection_sha256
        or grant.issued_at.tzinfo is None
        or grant.issued_at.utcoffset() is None
        or grant.expires_at.tzinfo is None
        or grant.expires_at.utcoffset() is None
        or not grant.issued_at <= at < grant.expires_at
    ):
        raise ConfirmationRejected("confirmation proof does not match the current command")
