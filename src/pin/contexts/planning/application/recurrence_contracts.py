"""Ports for confirmed recurrence creation, generation, and exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol
from uuid import UUID

from pin.contexts.planning.domain.plans import ReminderOffset
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceProjection,
    RecurringPlanDraft,
)


@dataclass(frozen=True, slots=True)
class RecurrenceConfirmationProof:
    preview_id: UUID
    preview_version: int
    credential: str


@dataclass(frozen=True, slots=True)
class RecurrenceConfirmationGrant:
    owner_id: UUID
    preview_id: UUID
    preview_version: int
    producer_id: str
    approved_projection_sha256: str
    issued_at: datetime
    expires_at: datetime


class RecurrenceConfirmationVerifier(Protocol):
    """Trusted preview producer; consumption must share the caller's transaction."""

    async def verify_and_consume(
        self,
        proof: RecurrenceConfirmationProof,
        *,
        owner_id: UUID,
        approved_projection_sha256: str,
        at: datetime,
    ) -> RecurrenceConfirmationGrant | None: ...


class RecurrenceCommandStore(Protocol):
    async def lock_action(self, owner_id: UUID, action_key: str) -> None: ...

    async def get_receipt(self, owner_id: UUID, action_key: str) -> dict[str, object] | None: ...

    async def persist_series(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        approved_preferences_version: int,
        draft: RecurringPlanDraft,
        projection: RecurrenceProjection,
        effective_reminders: tuple[ReminderOffset, ...],
        default_zone: str,
        quiet_zone: str,
        quiet_start: time,
        quiet_end: time,
        at: datetime,
        horizon_date: date,
    ) -> dict[str, object]: ...

    async def get_series_snapshot(
        self, owner_id: UUID, series_id: UUID
    ) -> SeriesSnapshot | None: ...

    async def lock_series_snapshot(
        self, owner_id: UUID, series_id: UUID
    ) -> SeriesSnapshot | None: ...

    async def apply_exception(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        snapshot: SeriesSnapshot,
        change: RecurrenceExceptionDraft,
        projection: RecurrenceProjection,
        new_draft: RecurringPlanDraft,
        approved_preferences_version: int,
        quiet_zone: str,
        quiet_start: time,
        quiet_end: time,
        approval_projection_sha256: str,
        at: datetime,
    ) -> dict[str, object]: ...

    async def stop_series(
        self,
        *,
        owner_id: UUID,
        action_key: str,
        payload_sha256: str,
        snapshot: SeriesSnapshot,
        at: datetime,
    ) -> dict[str, object]: ...


class RecurrenceClock(Protocol):
    def now(self) -> datetime: ...


class RecurrenceGeneratorStore(Protocol):
    async def get_series_snapshot(
        self, owner_id: UUID, series_id: UUID
    ) -> SeriesSnapshot | None: ...

    async def generate_occurrences(
        self,
        *,
        owner_id: UUID,
        series_id: UUID,
        expected_version: int,
        through: date,
        at: datetime,
        projection: RecurrenceProjection,
        approved_preferences_version: int,
        max_occurrences: int = 1000,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class SeriesOccurrenceSnapshot:
    sequence: int
    occurrence_id: UUID
    recurrence_key: str
    state: str
    version: int
    local_date: date


@dataclass(frozen=True, slots=True)
class SeriesSnapshot:
    id: UUID
    owner_id: UUID
    plan_item_id: UUID
    version: int
    state: str
    anchor_date: date
    anchor_sequence: int
    next_sequence: int
    total_count: int | None
    end_date: date | None
    draft: RecurringPlanDraft
    effective_reminders: tuple[ReminderOffset, ...]
    default_zone: str
    occurrences: tuple[SeriesOccurrenceSnapshot, ...]
    approved_preferences_version: int | None = None
    quiet_zone: str | None = None
    quiet_start: time | None = None
    quiet_end: time | None = None

