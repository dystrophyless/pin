"""Strict provider and caller DTOs for bounded conversation interpretation."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pin.contexts.conversation.domain.proposals import (
    FailureCategory,
    InputKind,
    ProposalOperation,
    SourceLanguage,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TargetSnapshot(StrictModel):
    id: UUID
    version: Annotated[int, Field(ge=1)]
    kind: Literal["plan", "occurrence", "series"]
    title: Annotated[str, Field(min_length=1, max_length=240)]


class ScheduledReminderSnapshot(StrictModel):
    scheduled_at: datetime
    offset_kind: Literal["calendar_days", "elapsed_seconds"]
    offset_amount: Annotated[int, Field(ge=1)]
    source: Literal["explicit", "default"]

    @field_validator("scheduled_at")
    @classmethod
    def aware_scheduled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled reminder time must be timezone-aware")
        return value.astimezone(UTC)


class PlanSnapshot(StrictModel):
    id: UUID
    version: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=240)]
    due_at: datetime | None = None
    timezone: Annotated[str | None, Field(max_length=64)] = None
    ends_at: datetime | None = None
    status: Literal["open", "completed", "cancelled"]
    schedule_kind: Literal["task", "deadline", "appointment", "untimed"] | None = None
    series_id: UUID | None = None
    is_recurring: bool | None = None
    reminders: tuple[ScheduledReminderSnapshot, ...] = ()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else None

    @model_validator(mode="after")
    def recurrence_link_matches_flag(self) -> PlanSnapshot:
        if self.series_id is not None and self.is_recurring is False:
            raise ValueError("recurring plan must retain its series ID")
        if self.series_id is None and self.is_recurring is True:
            raise ValueError("recurring plan requires its series ID")
        return self


class PreferenceSnapshot(StrictModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    value: Annotated[str, Field(max_length=240)]


class ConversationContext(StrictModel):
    """Owner-filtered caller snapshot; identity never enters the provider prompt."""

    owner_id: UUID
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    language: SourceLanguage
    input_kind: InputKind
    text: Annotated[str, Field(min_length=1, max_length=8_000)]
    reference_date: date
    reference_instant: datetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    plans: Annotated[tuple[PlanSnapshot, ...], Field(max_length=50)] = ()
    preferences: Annotated[tuple[PreferenceSnapshot, ...], Field(max_length=32)] = ()
    targets: Annotated[tuple[TargetSnapshot, ...], Field(max_length=100)] = ()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _validate_timezone(value)

    @field_validator("reference_instant")
    @classmethod
    def aware_reference_instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_instant must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def reference_date_matches_instant(self) -> ConversationContext:
        local_date = self.reference_instant.astimezone(ZoneInfo(self.timezone)).date()
        if self.reference_date != local_date:
            raise ValueError("reference_date must match reference_instant in the profile timezone")
        return self

    @model_validator(mode="after")
    def unique_owned_ids(self) -> ConversationContext:
        ids = [target.id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("target IDs must be unique")
        plan_ids = {plan.id for plan in self.plans}
        if not plan_ids.issubset(set(ids)):
            raise ValueError("every plan must have a matching owner-filtered target")
        return self


class AppointmentSchedule(StrictModel):
    kind: Literal["appointment"]
    local_start: datetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    duration_minutes: Annotated[int, Field(ge=1, le=1_440)] | None = None
    local_end: datetime | None = None
    fold: Literal["earlier", "later"] | None = None
    end_fold: Literal["earlier", "later"] | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def preserve_local_times(self) -> AppointmentSchedule:
        if self.local_start.tzinfo is not None:
            raise ValueError("appointment local_start must not contain a UTC offset")
        if self.local_end is not None:
            if self.local_end.tzinfo is not None or self.local_end <= self.local_start:
                raise ValueError("appointment local_end must be a later local time")
        if self.local_end is not None and self.duration_minutes is not None:
            raise ValueError("choose appointment local_end or duration, not both")
        if self.local_end is None and self.end_fold is not None:
            raise ValueError("end_fold requires local_end")
        return self


class DeadlineSchedule(StrictModel):
    kind: Literal["deadline"]
    local_date: date
    local_time: time | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    fold: Literal["earlier", "later"] | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _validate_timezone(value)


class TaskSchedule(StrictModel):
    kind: Literal["task"]
    local_date: date | None = None
    local_time: time | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    timing: Literal["timed", "untimed", "unspecified"] = "unspecified"
    fold: Literal["earlier", "later"] | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else None

    @model_validator(mode="after")
    def preserve_timing_choice(self) -> TaskSchedule:
        if self.timing == "timed" and (self.local_date is None or self.local_time is None):
            raise ValueError("timed tasks require a local date and time")
        if self.timing == "untimed" and self.local_time is not None:
            raise ValueError("untimed tasks cannot include a local time")
        if self.timing == "unspecified" and self.local_time is not None:
            raise ValueError("a task with a time must declare timed intent")
        return self


PlanSchedule = Annotated[
    AppointmentSchedule | DeadlineSchedule | TaskSchedule,
    Field(discriminator="kind"),
]


class RecurrenceSpec(StrictModel):
    frequency: Literal["daily", "weekly", "monthly"]
    interval: Annotated[int, Field(ge=1, le=365)] = 1
    weekdays: tuple[Annotated[int, Field(ge=0, le=6)], ...] = ()
    month_day: Annotated[int, Field(ge=1, le=31)] | None = None
    last_day: bool = False
    ends_on: date | None = None
    occurrence_count: Annotated[int, Field(ge=1, le=10_000)] | None = None

    @model_validator(mode="after")
    def restricted_rule_shape(self) -> RecurrenceSpec:
        if self.frequency == "weekly" and not self.weekdays:
            raise ValueError("weekly recurrence requires at least one weekday")
        if self.frequency != "weekly" and self.weekdays:
            raise ValueError("weekdays apply only to weekly recurrence")
        if self.frequency == "monthly" and (self.month_day is None) == (not self.last_day):
            raise ValueError("monthly recurrence requires a day or the explicit last-day choice")
        if self.frequency != "monthly" and (self.month_day is not None or self.last_day):
            raise ValueError("monthly day fields apply only to monthly recurrence")
        if self.ends_on is not None and self.occurrence_count is not None:
            raise ValueError("recurrence cannot combine end date and occurrence count")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekly weekdays must be unique")
        return self


class RelativeReminder(StrictModel):
    kind: Literal["relative"]
    basis: Literal["calendar_day", "elapsed"]
    amount: Annotated[int, Field(ge=1, le=10_000)]
    unit: Literal["day", "hour", "minute"]
    provenance: Literal["default", "explicit"]

    @model_validator(mode="after")
    def distinct_time_semantics(self) -> RelativeReminder:
        if (self.basis, self.unit) not in (
            ("calendar_day", "day"),
            ("elapsed", "hour"),
            ("elapsed", "minute"),
        ):
            raise ValueError("calendar days and elapsed time are distinct reminder bases")
        return self


class ExplicitReminder(StrictModel):
    kind: Literal["explicit"]
    local_at: datetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    provenance: Literal["explicit"] = "explicit"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _validate_timezone(value)

    @field_validator("local_at")
    @classmethod
    def local_time_has_no_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("explicit reminder local_at must not contain a UTC offset")
        return value


ReminderIntent = Annotated[
    RelativeReminder | ExplicitReminder,
    Field(discriminator="kind"),
]


class ProposalFields(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=240)] | None = None
    description: Annotated[str, Field(max_length=2_000)] | None = None
    schedule: PlanSchedule | None = None
    recurrence: RecurrenceSpec | None = None
    reminders: Annotated[tuple[ReminderIntent, ...], Field(max_length=16)] | None = None
    reminder_mode: Literal["override", "only"] | None = None


class Reply(StrictModel):
    kind: Literal["reply"]
    language: SourceLanguage
    text: Annotated[str, Field(min_length=1, max_length=4_000)]


class SuggestedStep(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    reason: Annotated[str, Field(min_length=1, max_length=400)]


class Advice(StrictModel):
    """Structured, non-persistent planning advice with stable selection order."""

    kind: Literal["advice"]
    language: SourceLanguage
    summary: Annotated[str, Field(min_length=1, max_length=1_200)]
    steps: Annotated[tuple[SuggestedStep, ...], Field(min_length=1, max_length=8)]

    def rendered_text(self) -> str:
        english = self.language.value == "en"
        heading = "Suggested steps:" if english else "Предлагаемые шаги:"
        rows = [self.summary.strip(), heading]
        for index, step in enumerate(self.steps, start=1):
            rows.append(f"{index}. {step.title.strip()} — {step.reason.strip()}")
        return "\n".join(rows)

    def to_reply(self) -> Reply:
        return Reply(kind="reply", language=self.language, text=self.rendered_text())

    @model_validator(mode="after")
    def is_renderable_as_reply(self) -> Advice:
        if not self.summary.strip() or any(
            not step.title.strip() or not step.reason.strip() for step in self.steps
        ):
            raise ValueError("advice text fields must not be whitespace-only")
        if len(self.rendered_text()) > 4_000:
            raise ValueError("rendered advice exceeds the durable reply limit")
        return self


class ActionProposal(StrictModel):
    kind: Literal["proposal"]
    language: SourceLanguage
    operation: ProposalOperation
    target_id: UUID | None = None
    expected_version: Annotated[int, Field(ge=1)] | None = None
    fields: ProposalFields

    @model_validator(mode="after")
    def has_action_shape(self) -> ActionProposal:
        if self.operation is ProposalOperation.CREATE_PLAN:
            if self.target_id is not None or self.expected_version is not None:
                raise ValueError("create proposals cannot target an existing object")
            if self.fields.title is None:
                raise ValueError("create proposals require a title")
            changed_fields = self.fields.model_fields_set
            if "reminder_mode" in changed_fields and (
                "reminders" not in changed_fields
                or self.fields.reminders is None
                or self.fields.reminder_mode is None
            ):
                raise ValueError("a create reminder mode requires a non-null reminder list")
        else:
            if self.target_id is None or self.expected_version is None:
                raise ValueError("change proposals require a target and expected version")
            changed_fields = self.fields.model_fields_set
            if "reminder_mode" in changed_fields:
                raise ValueError("reminder_mode is only supported for plan creation")
            if self.operation in (ProposalOperation.COMPLETE, ProposalOperation.CANCEL):
                if changed_fields:
                    raise ValueError("complete and cancel proposals cannot include changed fields")
                return self
            if not changed_fields:
                raise ValueError("change proposals require at least one changed field")
            if "title" in changed_fields and self.fields.title is None:
                raise ValueError("plan title cannot be cleared")
        return self


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be an IANA time zone") from exc
    return value


class ProposalBatch(StrictModel):
    """One fully typed, create-only batch that is previewed and confirmed atomically."""

    kind: Literal["proposal_batch"]
    schema_version: Literal[1]
    language: SourceLanguage
    actions: Annotated[tuple[ActionProposal, ...], Field(min_length=2, max_length=32)]

    @model_validator(mode="after")
    def creates_only_in_one_language(self) -> ProposalBatch:
        if any(
            action.operation is not ProposalOperation.CREATE_PLAN
            or action.language is not self.language
            for action in self.actions
        ):
            raise ValueError("a proposal batch contains create actions in one source language")
        return self


ConversationResult = Annotated[
    Reply | Advice | ActionProposal | ProposalBatch, Field(discriminator="kind")
]


class ModelFailure(StrictModel):
    kind: Literal["failure"] = "failure"
    category: FailureCategory
    provider_request_id: str | None = None
    usage_recorded: bool = False


class ModelSuccess(StrictModel):
    kind: Literal["success"] = "success"
    result: Reply | Advice | ActionProposal | ProposalBatch
    provider_request_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=4)]


ModelOutcome = ModelSuccess | ModelFailure
