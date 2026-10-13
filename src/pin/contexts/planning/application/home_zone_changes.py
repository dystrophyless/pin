"""Owner-confirmed home-zone changes and the atomic Planning handoff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from pin.contexts.identity.api import IdentityStore, Preferences
from pin.contexts.planning.application.home_zone_contracts import (
    HomeZoneChangeStore,
    HomeZoneProjection,
    SavedHomeZonePreview,
)
from pin.contexts.planning.domain.plans import ReminderOffset

PREVIEW_TTL = timedelta(hours=24)


class HomeZoneChangeRejected(ValueError):
    """A zone-change preview is stale, incomplete, or not authorized."""


@dataclass(frozen=True, slots=True)
class HomeZoneChangeProof:
    preview_id: UUID
    preview_version: int
    token: str

    def __post_init__(self) -> None:
        if self.preview_id.int == 0 or self.preview_version < 1 or not self.token.strip():
            raise ValueError("preview confirmation proof is incomplete")


@dataclass(frozen=True, slots=True)
class HomeZoneChangeGrant:
    owner_id: UUID
    preview_id: UUID
    preview_version: int
    projection_sha256: str
    expires_at: datetime


class HomeZoneChangeVerifier(Protocol):
    """Top-level owner-bound, one-time confirmation proof verifier."""

    async def verify_and_consume(
        self,
        proof: HomeZoneChangeProof,
        *,
        owner_id: UUID,
        projection_sha256: str,
        at: datetime,
    ) -> HomeZoneChangeGrant | None: ...


class HomeZoneChangeClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class HomeZoneChangeRequest:
    owner_id: UUID
    expected_profile_version: int
    preferences: Preferences
    reinterpret_occurrence_ids: tuple[UUID, ...] = ()
    reinterpret_series_ids: tuple[UUID, ...] = ()
    reminder_overrides: tuple[tuple[UUID, tuple[ReminderOffset, ...]], ...] = ()

    def __post_init__(self) -> None:
        if self.owner_id.int == 0:
            raise ValueError("owner_id is required")
        if type(self.expected_profile_version) is not int or self.expected_profile_version < 1:
            raise ValueError("expected_profile_version must be a positive integer")
        if len(set(self.reinterpret_occurrence_ids)) != len(self.reinterpret_occurrence_ids):
            raise ValueError("selected occurrence IDs must be unique")
        if len(set(self.reinterpret_series_ids)) != len(self.reinterpret_series_ids):
            raise ValueError("selected series IDs must be unique")
        override_ids = tuple(key for key, _ in self.reminder_overrides)
        if len(set(override_ids)) != len(override_ids):
            raise ValueError("reminder override IDs must be unique")


@dataclass(frozen=True, slots=True)
class HomeZoneChangePreview:
    preview_id: UUID
    preview_version: int
    owner_id: UUID
    expected_profile_version: int
    candidate_preferences: Preferences
    projection_sha256: str
    expires_at: datetime
    projection: HomeZoneProjection


async def preview_home_zone_change(
    request: HomeZoneChangeRequest,
    *,
    store: HomeZoneChangeStore,
    identity: IdentityStore,
    clock: HomeZoneChangeClock,
) -> HomeZoneChangePreview:
    """Persist a complete owner-fenced future projection without changing plans."""
    now = _now(clock)
    current = await identity.get_preferences(request.owner_id)
    if current is None or current.owner_id != request.owner_id:
        raise HomeZoneChangeRejected("a current owner profile is required")
    if current.version != request.expected_profile_version:
        raise HomeZoneChangeRejected("profile changed before the home-zone preview")
    if request.preferences.version != current.version + 1:
        raise ValueError("candidate preference version must advance the current profile once")
    projection = await store.project(
        owner_id=request.owner_id,
        at=now,
        current_home_zone=current.home_zone,
        preferences=request.preferences,
        reinterpret_occurrence_ids=request.reinterpret_occurrence_ids,
        reinterpret_series_ids=request.reinterpret_series_ids,
        reminder_overrides=dict(request.reminder_overrides),
        lock=False,
    )
    preview_id = uuid4()
    expires_at = now + PREVIEW_TTL
    digest = _digest(
        {
            "preview_id": str(preview_id),
            "owner_id": str(request.owner_id),
            "profile_version": current.version,
            "candidate": _preferences_payload(request.preferences),
            "projection": projection.payload,
        }
    )
    record = SavedHomeZonePreview(
        id=preview_id,
        owner_id=request.owner_id,
        profile_version=current.version,
        preview_version=1,
        projection_sha256=digest,
        payload={
            "candidate": _preferences_payload(request.preferences),
            "reinterpret_occurrence_ids": sorted(
                str(item) for item in request.reinterpret_occurrence_ids
            ),
            "reinterpret_series_ids": sorted(str(item) for item in request.reinterpret_series_ids),
            "reminder_overrides": _offset_overrides_payload(dict(request.reminder_overrides)),
            "projection": projection.payload,
        },
        state="pending",
        created_at=now,
        expires_at=expires_at,
    )
    await store.save_preview(record)
    return HomeZoneChangePreview(
        preview_id,
        1,
        request.owner_id,
        current.version,
        request.preferences,
        digest,
        expires_at,
        projection,
    )


async def confirm_home_zone_change(
    proof: HomeZoneChangeProof,
    *,
    authenticated_owner_id: UUID,
    store: HomeZoneChangeStore,
    identity: IdentityStore,
    verifier: HomeZoneChangeVerifier,
    clock: HomeZoneChangeClock,
) -> dict[str, object]:
    """Apply profile and all selected Planning effects in one caller-owned UoW."""
    current = await identity.lock_preferences(authenticated_owner_id)
    if current is None or current.owner_id != authenticated_owner_id:
        raise HomeZoneChangeRejected("a current owner profile is required")
    preview = await store.lock_preview(authenticated_owner_id, proof.preview_id)
    now = _now(clock)
    if (
        preview is None
        or preview.state != "pending"
        or preview.preview_version != proof.preview_version
        or preview.expires_at <= now
        or preview.profile_version != current.version
    ):
        raise HomeZoneChangeRejected("home-zone preview is expired or stale")
    candidate = _preferences_from_payload(preview.payload["candidate"])
    reinterpret_occurrence_ids = _uuid_sequence(preview.payload.get("reinterpret_occurrence_ids"))
    reinterpret_series_ids = _uuid_sequence(preview.payload.get("reinterpret_series_ids"))
    reminder_overrides = _offset_overrides_from_payload(preview.payload["reminder_overrides"])
    projection = await store.project(
        owner_id=authenticated_owner_id,
        at=now,
        current_home_zone=current.home_zone,
        preferences=candidate,
        reinterpret_occurrence_ids=reinterpret_occurrence_ids,
        reinterpret_series_ids=reinterpret_series_ids,
        reminder_overrides=reminder_overrides,
        lock=True,
    )
    expected_digest = _digest(
        {
            "preview_id": str(preview.id),
            "owner_id": str(authenticated_owner_id),
            "profile_version": preview.profile_version,
            "candidate": _preferences_payload(candidate),
            "projection": projection.payload,
        }
    )
    if expected_digest != preview.projection_sha256:
        raise HomeZoneChangeRejected("the approved home-zone projection is stale")
    grant = await verifier.verify_and_consume(
        proof,
        owner_id=authenticated_owner_id,
        projection_sha256=expected_digest,
        at=now,
    )
    if not _valid_grant(grant, proof, authenticated_owner_id, expected_digest, now):
        raise HomeZoneChangeRejected("confirmation proof does not authorize this preview")
    if projection.unresolved_reminder_targets:
        raise HomeZoneChangeRejected(
            "choose reminder offsets for legacy plans before confirming this profile change"
        )
    saved = await identity.save_preferences(
        owner_id=authenticated_owner_id,
        expected_version=preview.profile_version,
        preferences=candidate,
    )
    if saved is None:
        raise HomeZoneChangeRejected("profile changed before the zone change was saved")
    result = await store.apply_projection(
        owner_id=authenticated_owner_id,
        projection=projection,
        candidate_preferences=candidate,
        at=now,
    )
    await store.mark_preview_confirmed(
        owner_id=authenticated_owner_id, preview_id=preview.id, at=now
    )
    return {
        "preview_id": str(preview.id),
        "owner_id": str(authenticated_owner_id),
        "profile_version": saved.version,
        "projection_sha256": expected_digest,
        "result": result,
    }


def _valid_grant(
    grant: HomeZoneChangeGrant | None,
    proof: HomeZoneChangeProof,
    owner_id: UUID,
    digest: str,
    now: datetime,
) -> bool:
    return bool(
        grant is not None
        and grant.owner_id == owner_id
        and grant.preview_id == proof.preview_id
        and grant.preview_version == proof.preview_version
        and grant.projection_sha256 == digest
        and grant.expires_at.tzinfo is not None
        and grant.expires_at.utcoffset() is not None
        and now < grant.expires_at
    )


def _now(clock: HomeZoneChangeClock) -> datetime:
    value = clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _digest(value: dict[str, object]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _preferences_payload(value: Preferences) -> dict[str, object]:
    return {
        "language": value.language,
        "home_zone": value.home_zone,
        "reminder_calendar_days": value.reminder_calendar_days,
        "reminder_elapsed_hours": value.reminder_elapsed_hours,
        "quiet_start": value.quiet_start.isoformat(),
        "quiet_end": value.quiet_end.isoformat(),
        "review_time": value.review_time.isoformat(),
        "version": value.version,
    }


def _preferences_from_payload(value: object) -> Preferences:
    if not isinstance(value, dict):
        raise HomeZoneChangeRejected("stored candidate preferences are invalid")
    return Preferences(
        language=str(value["language"]),
        home_zone=str(value["home_zone"]),
        reminder_calendar_days=int(value["reminder_calendar_days"]),
        reminder_elapsed_hours=int(value["reminder_elapsed_hours"]),
        quiet_start=datetime.strptime(str(value["quiet_start"]), "%H:%M:%S").time(),
        quiet_end=datetime.strptime(str(value["quiet_end"]), "%H:%M:%S").time(),
        review_time=datetime.strptime(str(value["review_time"]), "%H:%M:%S").time(),
        version=int(value["version"]),
    )


def _offset_overrides_payload(
    values: dict[UUID, tuple[ReminderOffset, ...]],
) -> dict[str, list[dict[str, object]]]:
    return {
        str(key): [
            {"kind": item.kind.value, "amount": item.amount, "source": item.source}
            for item in offsets
        ]
        for key, offsets in sorted(values.items(), key=lambda pair: str(pair[0]))
    }


def _offset_overrides_from_payload(value: object) -> dict[UUID, tuple[ReminderOffset, ...]]:
    if not isinstance(value, dict):
        raise HomeZoneChangeRejected("stored reminder overrides are invalid")
    from pin.contexts.planning.domain.plans import OffsetKind

    return {
        UUID(str(key)): tuple(
            ReminderOffset(OffsetKind(str(item["kind"])), int(item["amount"]), str(item["source"]))
            for item in entries
        )
        for key, entries in value.items()
    }


def _uuid_sequence(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HomeZoneChangeRejected("stored home-zone selection is invalid")
    try:
        return tuple(UUID(item) for item in value)
    except ValueError:
        raise HomeZoneChangeRejected("stored home-zone selection is invalid") from None
