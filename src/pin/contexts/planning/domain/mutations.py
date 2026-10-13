"""Framework-independent preview vocabulary for deterministic plan mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from pin.contexts.planning.domain.plans import (
    OccurrenceState,
    OneOffPlanDraft,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ReminderSchedule,
    ResolvedSchedule,
)
from pin.contexts.planning.domain.recurrence import (
    RecurrenceExceptionDraft,
    RecurrenceProjection,
    RecurringPlanDraft,
)


class MutationAction(StrEnum):
    COMPLETE = "complete"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"


class MutationScope(StrEnum):
    THIS_OCCURRENCE = "this_occurrence"
    THIS_AND_FUTURE = "this_and_future"


@dataclass(frozen=True, slots=True)
class MutationTarget:
    owner_id: UUID
    occurrence_id: UUID
    plan_item_id: UUID
    title: str
    plan_version: int
    occurrence_version: int
    approved_preferences_version: int | None
    state: OccurrenceState
    schedule: ResolvedSchedule
    reminder_offsets: tuple[ReminderOffset, ...] = ()
    recurrence_series_id: UUID | None = None
    recurrence_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.owner_id.int == 0:
            raise ValueError("owner_id is required")
        if self.plan_version < 1 or self.occurrence_version < 1:
            raise ValueError("target versions must be positive")
        if self.approved_preferences_version is not None and self.approved_preferences_version < 1:
            raise ValueError("approved preference version must be positive")
        if not self.title.strip():
            raise ValueError("target title is required")
        if (self.recurrence_series_id is None) != (self.recurrence_sequence is None):
            raise ValueError("recurring targets require both series ID and sequence")
        if self.recurrence_sequence is not None and self.recurrence_sequence < 0:
            raise ValueError("recurrence sequence cannot be negative")


@dataclass(frozen=True, slots=True)
class MutationRequest:
    owner_id: UUID
    occurrence_id: UUID
    expected_occurrence_version: int
    action: MutationAction
    scope: MutationScope = MutationScope.THIS_OCCURRENCE
    replacement: OneOffPlanDraft | None = None

    def __post_init__(self) -> None:
        if self.owner_id.int == 0 or self.expected_occurrence_version < 1:
            raise ValueError("mutation owner and expected version are required")
        if (self.action is MutationAction.RESCHEDULE) != (self.replacement is not None):
            raise ValueError("only reschedule mutations require a replacement schedule")
        if (
            self.action is not MutationAction.RESCHEDULE
            and self.action is MutationAction.COMPLETE
            and self.scope is not MutationScope.THIS_OCCURRENCE
        ):
            raise ValueError("completion applies to one occurrence only")


@dataclass(frozen=True, slots=True)
class MutationSeriesProjection:
    series_version: int
    change: RecurrenceExceptionDraft
    updated_draft: RecurringPlanDraft
    projection: RecurrenceProjection
    through: date
    remaining_count: int | None
    approval_projection_sha256: str

    def __post_init__(self) -> None:
        if self.series_version < 1:
            raise ValueError("series_version must be positive")
        if not self.approval_projection_sha256:
            raise ValueError("series projection approval digest is required")


@dataclass(frozen=True, slots=True)
class MutationCancellationTarget:
    occurrence_id: UUID
    sequence: int
    recurrence_key: str
    version: int
    local_date: date


@dataclass(frozen=True, slots=True)
class MutationCancellationProjection:
    """Versioned, owner-bound set of generated future rows to cancel atomically."""

    series_version: int
    targets: tuple[MutationCancellationTarget, ...]
    approval_projection_sha256: str

    def __post_init__(self) -> None:
        if self.series_version < 1 or not self.targets:
            raise ValueError("future cancellation requires a versioned non-empty target set")
        if not self.approval_projection_sha256:
            raise ValueError("future cancellation approval digest is required")


@dataclass(frozen=True, slots=True)
class MutationReminderProjection:
    scheduled: tuple[ReminderSchedule, ...]
    omitted: tuple[ReminderOmission, ...]
    notices: tuple[ReminderNotice, ...]


@dataclass(frozen=True, slots=True)
class MutationPreview:
    preview_id: UUID
    owner_id: UUID
    target: MutationTarget
    request: MutationRequest
    replacement_schedule: ResolvedSchedule | None
    series_projection: MutationSeriesProjection | None
    cancellation_projection: MutationCancellationProjection | None
    reminder_projection: MutationReminderProjection | None
    series_preview_schedules: tuple[ResolvedSchedule, ...]
    expected_preferences_version: int
    preview_version: int
    created_at: datetime
    expires_at: datetime
    approval_projection_sha256: str

    def __post_init__(self) -> None:
        if self.owner_id != self.target.owner_id or self.owner_id != self.request.owner_id:
            raise ValueError("preview, target, and request must have the same owner")
        if self.request.occurrence_id != self.target.occurrence_id:
            raise ValueError("preview target does not match the request")
        if self.request.expected_occurrence_version != self.target.occurrence_version:
            raise ValueError("preview target version does not match the request")
        if self.preview_version < 1:
            raise ValueError("preview_version must be positive")
        if self.expected_preferences_version < 1:
            raise ValueError("expected_preferences_version must be positive")
        for value in (self.created_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("preview timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("preview must expire after creation")
        if (self.request.action is MutationAction.RESCHEDULE) != (
            self.replacement_schedule is not None
        ):
            raise ValueError("preview schedule does not match the requested action")
        if (
            self.request.action is MutationAction.RESCHEDULE
            and self.target.recurrence_series_id is None
            and self.series_projection is not None
        ):
            raise ValueError("one-off mutation cannot contain a series projection")
        requires_future_cancel = (
            self.request.action is MutationAction.CANCEL
            and self.request.scope is MutationScope.THIS_AND_FUTURE
        )
        if requires_future_cancel != (self.cancellation_projection is not None):
            raise ValueError("future cancellation preview must bind its selected occurrences")


def ensure_preview_current(
    preview: MutationPreview,
    current: MutationTarget,
    *,
    owner_id: UUID,
    at: datetime,
) -> None:
    """Reject foreign, expired, changed, or terminal targets before a write."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    if preview.owner_id != owner_id or current.owner_id != owner_id:
        raise ValueError("mutation preview belongs to another owner")
    if at.astimezone(UTC) >= preview.expires_at.astimezone(UTC):
        raise ValueError("mutation preview expired")
    if (
        current.occurrence_id != preview.target.occurrence_id
        or current.occurrence_version != preview.target.occurrence_version
        or current.plan_item_id != preview.target.plan_item_id
        or current.recurrence_series_id != preview.target.recurrence_series_id
        or current.recurrence_sequence != preview.target.recurrence_sequence
    ):
        raise ValueError("mutation target changed after preview")
    if current.state is not OccurrenceState.OPEN:
        raise ValueError("completed or cancelled occurrences cannot be changed")

