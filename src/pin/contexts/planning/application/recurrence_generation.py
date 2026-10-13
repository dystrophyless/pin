"""Bounded, restartable recurrence generation command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, timedelta
from uuid import UUID

from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceClock,
    RecurrenceGeneratorStore,
)


@dataclass(frozen=True, slots=True)
class GenerateRecurrenceSeries:
    series_id: UUID
    expected_series_version: int
    through: date
    max_occurrences: int = 1000


async def generate_recurrence_horizon(
    command: GenerateRecurrenceSeries,
    *,
    authenticated_owner_id: UUID,
    store: RecurrenceGeneratorStore,
    clock: RecurrenceClock,
) -> dict[str, object]:
    at = clock.now()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    at = at.astimezone(UTC)
    if not 1 <= command.max_occurrences <= 1000:
        raise ValueError("generation batches are bounded at 1,000 occurrences")
    if command.through < at.date() or command.through > at.date() + timedelta(days=366):
        raise ValueError("generation horizon must be within the next 366 calendar days")
    return await store.generate_occurrences(
        owner_id=authenticated_owner_id,
        series_id=command.series_id,
        expected_version=command.expected_series_version,
        through=command.through,
        at=at,
        max_occurrences=command.max_occurrences,
    )
