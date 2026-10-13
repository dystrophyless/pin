"""Planning-owned ports and immutable DTOs for home-zone change confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pin.contexts.identity.api import Preferences
from pin.contexts.planning.domain.plans import ReminderOffset


@dataclass(frozen=True, slots=True)
class HomeZoneReminderChoiceTarget:
    target_type: str
    target_id: UUID
    title: str


@dataclass(frozen=True, slots=True)
class HomeZoneProjection:
    payload: dict[str, object]
    unresolved_reminder_targets: tuple[HomeZoneReminderChoiceTarget, ...]


@dataclass(frozen=True, slots=True)
class SavedHomeZonePreview:
    id: UUID
    owner_id: UUID
    profile_version: int
    preview_version: int
    projection_sha256: str
    payload: dict[str, object]
    state: str
    created_at: datetime
    expires_at: datetime


class HomeZoneChangeStore(Protocol):
    """Planning persistence behind a caller-owned unit of work."""

    async def save_preview(self, preview: SavedHomeZonePreview) -> None: ...

    async def lock_preview(
        self, owner_id: UUID, preview_id: UUID
    ) -> SavedHomeZonePreview | None: ...

    async def project(
        self,
        *,
        owner_id: UUID,
        at: datetime,
        current_home_zone: str,
        preferences: Preferences,
        reinterpret_occurrence_ids: tuple[UUID, ...],
        reinterpret_series_ids: tuple[UUID, ...],
        reminder_overrides: dict[UUID, tuple[ReminderOffset, ...]],
        lock: bool,
    ) -> HomeZoneProjection: ...

    async def apply_projection(
        self,
        *,
        owner_id: UUID,
        projection: HomeZoneProjection,
        candidate_preferences: Preferences,
        at: datetime,
    ) -> dict[str, object]: ...

    async def mark_preview_confirmed(
        self, *, owner_id: UUID, preview_id: UUID, at: datetime
    ) -> None: ...
