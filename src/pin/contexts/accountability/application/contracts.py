"""Published Accountability read and delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol
from uuid import UUID

from pin.contexts.accountability.domain.cycles import CycleKind, CycleState, MessageSlot


@dataclass(frozen=True, slots=True)
class ReviewProfile:
    owner_id: UUID
    home_zone: str
    language: str
    review_time: time
    quiet_start: time
    quiet_end: time
    destination_id: int


@dataclass(frozen=True, slots=True)
class OpenUntimedItem:
    occurrence_id: UUID
    title: str
    local_date: date


@dataclass(frozen=True, slots=True)
class DailyReviewPage:
    review_id: UUID
    owner_id: UUID
    language: str
    page_number: int
    page_count: int
    total_items: int
    items: tuple[OpenUntimedItem, ...]


@dataclass(frozen=True, slots=True)
class PlannedCheckIn:
    owner_id: UUID
    occurrence_id: UUID
    occurrence_version: int
    kind: CycleKind
    title: str
    due_at: datetime
    state: CycleState


@dataclass(frozen=True, slots=True)
class CurrentOccurrence:
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


@dataclass(frozen=True, slots=True)
class AccountabilitySend:
    intent_id: UUID
    owner_id: UUID
    slot: MessageSlot
    destination_id: int
    language: str
    title: str | None
    occurrence_ids: tuple[UUID, ...]
    cycle_id: UUID | None
    review_id: UUID | None
    plain_text: str
    dispatch_version: int = 1


class ReviewProfileReader(Protocol):
    async def list_review_profiles(self) -> tuple[ReviewProfile, ...]: ...


class TodayUntimedReader(Protocol):
    async def list_today_open_untimed(
        self, owner_id: UUID, *, profile_zone: str, at: datetime, for_update: bool = False
    ) -> tuple[OpenUntimedItem, ...]: ...


class CurrentOccurrenceReader(Protocol):
    async def get_current_occurrence(
        self, owner_id: UUID, occurrence_id: UUID, *, for_update: bool = False
    ) -> CurrentOccurrence | None: ...
