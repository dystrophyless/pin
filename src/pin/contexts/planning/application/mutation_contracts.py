"""Caller-UoW ports for mutation previews and their confirmed effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pin.contexts.billing.api import AccessUsageStore
from pin.contexts.identity.api import IdentityStore, PreferencesSnapshot
from pin.contexts.planning.application.recurrence_contracts import SeriesSnapshot
from pin.contexts.planning.domain.mutations import MutationPreview, MutationTarget


@dataclass(frozen=True, slots=True)
class MutationProof:
    preview_id: UUID
    preview_version: int
    token: str


@dataclass(frozen=True, slots=True)
class MutationGrant:
    owner_id: UUID
    preview_id: UUID
    preview_version: int
    projection_sha256: str
    expires_at: datetime


class MutationClock(Protocol):
    def now(self) -> datetime: ...


class MutationConfirmationVerifier(Protocol):
    async def verify_and_consume(
        self,
        proof: MutationProof,
        *,
        owner_id: UUID,
        projection_sha256: str,
        at: datetime,
    ) -> MutationGrant | None: ...


class MutationStore(Protocol):
    """Planning-owned, caller-UoW storage for version-fenced mutations."""

    async def get_target(
        self, *, owner_id: UUID, occurrence_id: UUID, lock: bool = False
    ) -> MutationTarget | None: ...

    async def save_preview(self, preview: MutationPreview) -> None: ...

    async def get_receipt(
        self, *, owner_id: UUID, preview_id: UUID
    ) -> dict[str, object] | None: ...

    async def get_preview(
        self, *, owner_id: UUID, preview_id: UUID, lock: bool = False
    ) -> MutationPreview | None: ...

    async def get_series_snapshot(
        self, *, owner_id: UUID, series_id: UUID, lock: bool = False
    ) -> SeriesSnapshot | None: ...

    async def apply(
        self, preview: MutationPreview, *, preferences: PreferencesSnapshot, at: datetime
    ) -> dict[str, object]: ...


__all__ = [
    "MutationAccessStore",
    "MutationClock",
    "MutationConfirmationVerifier",
    "MutationGrant",
    "MutationIdentityStore",
    "MutationProof",
    "MutationStore",
]

MutationIdentityStore = IdentityStore
MutationAccessStore = AccessUsageStore

