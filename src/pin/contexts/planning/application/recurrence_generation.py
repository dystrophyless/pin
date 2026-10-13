"""Bounded, restartable recurrence generation command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, timedelta
from uuid import UUID

from pin.contexts.identity.api import IdentityStore
from pin.contexts.planning.application.recurrence_contracts import (
    RecurrenceClock,
    RecurrenceGeneratorStore,
)
from pin.contexts.planning.application.reminder_policy import apply_recurrence_quiet_policy
from pin.contexts.planning.domain.recurrence import project_recurrence


@dataclass(frozen=True, slots=True)
class GenerateRecurrenceSeries:
    series_id: UUID
    expected_series_version: int
    through: date
    max_occurrences: int = 1000


class RecurrenceGenerationPolicyStale(ValueError):
    """The stored series policy no longer matches current owner preferences."""


async def generate_recurrence_horizon(
    command: GenerateRecurrenceSeries,
    *,
    authenticated_owner_id: UUID,
    store: RecurrenceGeneratorStore,
    identity: IdentityStore,
    clock: RecurrenceClock,
) -> dict[str, object]:
    preferences = await identity.lock_preferences(authenticated_owner_id)
    if preferences is None or preferences.owner_id != authenticated_owner_id:
        raise RecurrenceGenerationPolicyStale("current owner preferences are required")
    at = clock.now()
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    at = at.astimezone(UTC)
    if not 1 <= command.max_occurrences <= 1000:
        raise ValueError("generation batches are bounded at 1,000 occurrences")
    if command.through < at.date() or command.through > at.date() + timedelta(days=366):
        raise ValueError("generation horizon must be within the next 366 calendar days")
    snapshot = await store.get_series_snapshot(authenticated_owner_id, command.series_id)
    if snapshot is None:
        raise ValueError("series is missing or belongs to another owner")
    if snapshot.version != command.expected_series_version:
        raise ValueError("series changed after generation was requested")
    if snapshot.state != "active":
        raise ValueError("a stopped series cannot generate occurrences")
    if (
        snapshot.approved_preferences_version is None
        or snapshot.quiet_zone is None
        or snapshot.quiet_start is None
        or snapshot.quiet_end is None
    ):
        raise RecurrenceGenerationPolicyStale(
            "series reminder policy requires a newly confirmed projection"
        )
    if (
        preferences.version != snapshot.approved_preferences_version
        or preferences.home_zone != snapshot.default_zone
        or preferences.home_zone != snapshot.quiet_zone
        or preferences.quiet_start != snapshot.quiet_start
        or preferences.quiet_end != snapshot.quiet_end
    ):
        raise RecurrenceGenerationPolicyStale(
            "owner preferences changed; confirm an updated future schedule before generation"
        )
    projection = project_recurrence(
        snapshot.draft,
        owner_id=authenticated_owner_id,
        default_zone=snapshot.default_zone,
        default_reminders=snapshot.effective_reminders,
        at=at,
        through=command.through,
        max_occurrences=command.max_occurrences,
        anchor=snapshot.anchor_date,
        first_sequence=snapshot.next_sequence,
        anchor_sequence=snapshot.anchor_sequence,
        total_count=snapshot.total_count,
    )
    projection = apply_recurrence_quiet_policy(
        projection,
        now=at,
        quiet_zone=snapshot.quiet_zone,
        quiet_start=snapshot.quiet_start,
        quiet_end=snapshot.quiet_end,
    )
    return await store.generate_occurrences(
        owner_id=authenticated_owner_id,
        series_id=command.series_id,
        expected_version=command.expected_series_version,
        through=command.through,
        at=at,
        projection=projection,
        approved_preferences_version=snapshot.approved_preferences_version,
        max_occurrences=command.max_occurrences,
    )
