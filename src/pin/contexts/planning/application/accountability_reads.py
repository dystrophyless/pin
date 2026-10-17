from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AccountabilityOccurrence:
    owner_id: UUID
    occurrence_id: UUID
    occurrence_version: int
    kind: str
    state: str
    title: str
    event_zone: str
    local_date: date
    starts_at: datetime | None
    ends_at: datetime | None
    deadline_at: datetime | None
    source_created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountabilityOccurrencePage:
    items: tuple[AccountabilityOccurrence, ...]
    next_occurrence_id: UUID | None


class AccountabilityReadStore(Protocol):
    async def get_accountability_occurrence(
        self, *, owner_id: UUID, occurrence_id: UUID, for_update: bool = False
    ) -> AccountabilityOccurrence | None: ...

    async def list_today_open_untimed(
        self,
        *,
        owner_id: UUID,
        profile_zone: str,
        at: datetime,
        after_occurrence_id: UUID | None = None,
        limit: int = 100,
        for_update: bool = False,
    ) -> AccountabilityOccurrencePage: ...
