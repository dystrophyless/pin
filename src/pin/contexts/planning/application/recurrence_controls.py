"""Confirmed control projections for stopping recurrence generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class StopSeriesPreview:
    series_id: UUID
    series_version: int
    next_sequence: int
    future_occurrences: tuple[tuple[int, str, str], ...]
    approval_projection_sha256: str


@dataclass(frozen=True, slots=True)
class StopRecurrenceSeries:
    action_key: str
    series_id: UUID
    expected_series_version: int
    expected_preferences_version: int
    proof: RecurrenceConfirmationProof


def preview_stop_series(snapshot: SeriesSnapshot) -> StopSeriesPreview:
    if snapshot.state != "active":
        raise ValueError("only an active recurrence series can be stopped")
    future = tuple(
        (item.sequence, item.recurrence_key, item.local_date.isoformat())
        for item in snapshot.occurrences
        if item.state == "open"
    )
    projection = {
        "schema_version": 1,
        "operation": "stop_generation",
        "series_id": str(snapshot.id),
        "owner_id": str(snapshot.owner_id),
        "series_version": snapshot.version,
        "next_sequence": snapshot.next_sequence,
        "already_generated_open_occurrences_preserved": future,
        "preserved_total_count": snapshot.total_count,
        "end_date": snapshot.end_date.isoformat() if snapshot.end_date else None,
    }
    return StopSeriesPreview(
        snapshot.id,
        snapshot.version,
        snapshot.next_sequence,
        future,
        _digest(projection),
    )


async def stop_confirmed_series(
    command: StopRecurrenceSeries,
    *,
    authenticated_owner_id: UUID,
    store: RecurrenceCommandStore,
    identity: IdentityStore,
    billing: AccessUsageStore,
    confirmation_verifier: RecurrenceConfirmationVerifier,
    clock: RecurrenceClock,
) -> dict[str, object]:
    now = _now(clock)
    payload_sha = _digest(
        {
            "schema_version": 1,
            "operation": "stop_generation",
            "series_id": str(command.series_id),
            "expected_series_version": command.expected_series_version,
            "expected_preferences_version": command.expected_preferences_version,
        }
    )
    await store.lock_action(authenticated_owner_id, command.action_key)
    receipt = await store.get_receipt(authenticated_owner_id, command.action_key)
    if receipt is not None:
        if receipt.get("payload_sha256") != payload_sha:
            raise RecurrenceIdempotencyConflict("action key was reused for another series command")
        return receipt
    snapshot = await store.lock_series_snapshot(authenticated_owner_id, command.series_id)
    if snapshot is None:
        raise RecurrenceAccessDenied("series is missing or belongs to another owner")
    if snapshot.version != command.expected_series_version:
        raise RecurrenceStalePreview("series changed after stop preview")
    preferences = await identity.get_preferences(authenticated_owner_id)
    if (
        preferences is None
        or preferences.owner_id != authenticated_owner_id
        or preferences.version != command.expected_preferences_version
    ):
        raise RecurrenceStalePreview("current preferences do not match the stop preview")
    access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CANCEL_PLAN, now)
    )
    if not access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit series changes")
    preview = preview_stop_series(snapshot)
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
        raise RecurrenceStalePreview("current preferences changed after stop preview")
    current_access = await billing.check_access(
        CheckAccess(authenticated_owner_id, Operation.CANCEL_PLAN, write_at)
    )
    if not current_access.allowed:
        raise RecurrenceAccessDenied("current billing access does not permit series changes")
    if (
        preview_stop_series(snapshot).approval_projection_sha256
        != preview.approval_projection_sha256
    ):
        raise RecurrenceStalePreview("series state changed after stop preview")
    return await store.stop_series(
        owner_id=authenticated_owner_id,
        action_key=command.action_key,
        payload_sha256=payload_sha,
        snapshot=snapshot,
        at=write_at,
    )


def _check_grant(
    grant: RecurrenceConfirmationGrant | None,
    proof: RecurrenceConfirmationProof,
    owner_id: UUID,
    preview: StopSeriesPreview,
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
        raise RecurrenceConfirmationRejected("proof does not authorize stopping this series")


def _now(clock: RecurrenceClock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _digest(value: dict[str, object]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
