from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pin.contexts.identity.application.contracts import PreferencesSnapshot


@dataclass(frozen=True, slots=True)
class PreferencesPage:
    items: tuple[PreferencesSnapshot, ...]
    next_owner_id: UUID | None


class ReviewProfilesReadStore(Protocol):
    async def list_review_profiles(
        self, *, after_owner_id: UUID | None = None, limit: int = 100
    ) -> PreferencesPage: ...
