"""Pure calendar recurrence rules and stable occurrence projections."""

from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pin.contexts.planning.domain.plans import (
    OneOffPlanDraft,
    PlanKind,
    ReminderNotice,
    ReminderOffset,
    ReminderOmission,
    ResolvedSchedule,
    ScheduleNeedsClarification,
    propose_plan,
)


class RecurrenceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class RecurrenceExceptionScope(StrEnum):
    SINGLE = "single"
    THIS_AND_FUTURE = "this_and_future"


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    frequency: RecurrenceFrequency
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    monthly_day: int | None = None
    last_day: bool = False
    count: int | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.frequency, RecurrenceFrequency):
            raise ValueError("frequency must be a supported RecurrenceFrequency")
        if type(self.interval) is not int or self.interval < 1:
            raise ValueError("recurrence interval must be a positive integer")
        if self.count is not None and (type(self.count) is not int or self.count < 1):
            raise ValueError("recurrence count must be positive")
        if self.end_date is not None and type(self.end_date) is not date:
            raise ValueError("end_date must be a calendar date")
        if self.frequency is RecurrenceFrequency.WEEKLY:
            if not self.weekdays or len(set(self.weekdays)) != len(self.weekdays):
                raise ValueError("weekly recurrence requires unique weekdays")
            if any(type(day) is not int or not 0 <= day <= 6 for day in self.weekdays):
                raise ValueError("weekdays use Monday=0 through Sunday=6")
        elif self.weekdays:
            raise ValueError("only weekly recurrence accepts weekdays")
        if self.frequency is RecurrenceFrequency.MONTHLY:
            if self.last_day == (self.monthly_day is not None):
                raise ValueError("monthly recurrence requires a day or last_day")
            if self.monthly_day is not None and not 1 <= self.monthly_day <= 31:
                raise ValueError("monthly_day must be between 1 and 31")
        elif self.monthly_day is not None or self.last_day:
            raise ValueError("only monthly recurrence accepts a monthly day pattern")


@dataclass(frozen=True, slots=True)
class RecurringPlanDraft:
    kind: PlanKind
    title: str
    start_date: date
    local_time: time | None
    event_zone: str | None
    recurrence: RecurrenceRule
    duration_seconds: int | None = None
    end_local_time: time | None = None
    end_local_date: date | None = None
    fold: str | None = None
    end_fold: str | None = None
    reminders: tuple[ReminderOffset, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.start_date) is not date:
            raise ValueError("start_date must be a calendar date")
        if not isinstance(self.recurrence, RecurrenceRule):
            raise ValueError("recurrence is required")
        # Reuse one-off boundary validation, which also protects storage shape.
        OneOffPlanDraft(
            kind=self.kind,
            title=self.title,
            local_date=self.start_date,
            local_time=self.local_time,
            event_zone=self.event_zone,
            end_local_date=self.end_local_date,
            end_local_time=self.end_local_time,
            duration_seconds=self.duration_seconds,
            fold=self.fold,
            end_fold=self.end_fold,
            reminders=self.reminders,
        )
        if self.recurrence.end_date is not None and self.recurrence.end_date < self.start_date:
            raise ValueError("recurrence end_date cannot precede the first occurrence")
        if (
            self.recurrence.frequency is RecurrenceFrequency.WEEKLY
            and self.start_date.weekday() not in self.recurrence.weekdays
        ):
            raise ValueError("the first occurrence must match a selected weekday")
        if self.recurrence.frequency is RecurrenceFrequency.MONTHLY:
            if self.recurrence.last_day and self.start_date.day != monthrange(
                self.start_date.year, self.start_date.month
            )[1]:
                raise ValueError("a last-day recurrence must start on the month's last day")
            if (
                self.recurrence.monthly_day is not None
                and self.start_date.day != min(
                    self.recurrence.monthly_day,
                    monthrange(self.start_date.year, self.start_date.month)[1],
                )
            ):
                raise ValueError("the first occurrence must match its monthly day pattern")


@dataclass(frozen=True, slots=True)
class RecurrenceExceptionDraft:
    scope: RecurrenceExceptionScope
    sequence: int
    replacement: OneOffPlanDraft
    new_pattern: RecurrenceRule | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RecurrenceExceptionScope):
            raise ValueError("scope must be single or this_and_future")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.scope is RecurrenceExceptionScope.SINGLE and self.new_pattern is not None:
            raise ValueError("single-occurrence changes cannot change the recurrence pattern")


@dataclass(frozen=True, slots=True)
class RecurrenceOccurrence:
    sequence: int
    recurrence_key: str
    schedule: ResolvedSchedule
    reminders: tuple[tuple[ReminderOffset, datetime], ...]
    omitted_reminders: tuple[ReminderOmission, ...]
    dst_gap_shift_seconds: int = 0
    repeated_time_choice: str | None = None
    reminder_notices: tuple[ReminderNotice, ...] = ()


@dataclass(frozen=True, slots=True)
class RecurrenceProjection:
    dates: tuple[date, ...]
    occurrences: tuple[RecurrenceOccurrence, ...]
    truncated: bool
    fingerprint: str


def recurrence_date(anchor: date, rule: RecurrenceRule, step: int) -> date:
    """Return a recurrence date relative to an immutable anchor and original pattern."""
    if type(step) is not int or step < 0:
        raise ValueError("step must be non-negative")
    if step == 0:
        return anchor
    if rule.frequency is RecurrenceFrequency.DAILY:
        return anchor + timedelta(days=rule.interval * step)
    if rule.frequency is RecurrenceFrequency.WEEKLY:
        start_week = anchor - timedelta(days=anchor.weekday())
        weekdays = sorted(rule.weekdays)
        dates: list[date] = []
        week = 0
        while len(dates) <= step:
            if week % rule.interval == 0:
                for weekday in weekdays:
                    candidate = start_week + timedelta(weeks=week, days=weekday)
                    if candidate >= anchor:
                        dates.append(candidate)
            week += 1
        return dates[step]
    month_index = anchor.year * 12 + anchor.month - 1 + rule.interval * step
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    last = monthrange(year, month)[1]
    day = last if rule.last_day else min(rule.monthly_day or anchor.day, last)
    return date(year, month, day)


def _limit_count(rule: RecurrenceRule) -> int | None:
    if rule.count is not None:
        return rule.count
    return None


def project_recurrence(
    draft: RecurringPlanDraft,
    *,
    owner_id: UUID,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    at: datetime,
    through: date,
    max_occurrences: int = 64,
    anchor: date | None = None,
    first_sequence: int = 0,
    anchor_sequence: int = 0,
    total_count: int | None = None,
) -> RecurrenceProjection:
    """Resolve a bounded preview/generation window without writing state."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("projection timestamp must be timezone-aware")
    if max_occurrences < 1:
        raise ValueError("max_occurrences must be positive")
    if first_sequence < anchor_sequence or anchor_sequence < 0:
        raise ValueError("sequence cursor cannot precede its recurrence anchor")
    root = anchor or draft.start_date
    rule = draft.recurrence
    count = total_count if total_count is not None else _limit_count(rule)
    default_offsets = default_reminders
    results: list[RecurrenceOccurrence] = []
    dates: list[date] = []
    # Stop after a bounded number of candidates, even if a malformed/custom
    # frequency would otherwise fail to make progress.
    for offset in range(max_occurrences):
        sequence = first_sequence + offset
        step = first_sequence - anchor_sequence + offset
        local_date = recurrence_date(root, rule, step)
        if count is not None and sequence >= count:
            break
        if rule.end_date is not None and local_date > rule.end_date:
            break
        if local_date > through:
            break
        occurrence, gap_seconds, repeated_choice = _resolve_recurring_occurrence(
            draft,
            local_date,
            owner_id=owner_id,
            default_zone=default_zone,
            default_reminders=default_offsets,
            now=at,
            sequence=sequence,
        )
        dates.append(local_date)
        results.append(occurrence)
    truncated = False
    if len(results) == max_occurrences:
        next_sequence = first_sequence + len(results)
        next_date = recurrence_date(
            root, rule, first_sequence - anchor_sequence + len(results)
        )
        truncated = (
            (count is None or next_sequence < count)
            and (rule.end_date is None or next_date <= rule.end_date)
            and next_date <= through
        )
    return RecurrenceProjection(
        dates=tuple(dates),
        occurrences=tuple(results),
        truncated=truncated,
        fingerprint=recurrence_projection_digest(draft, results),
    )


def _resolve_recurring_occurrence(
    draft: RecurringPlanDraft,
    local_date: date,
    *,
    owner_id: UUID,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    now: datetime,
    sequence: int,
    replacement: OneOffPlanDraft | None = None,
) -> tuple[RecurrenceOccurrence, int, str | None]:
    from pin.contexts.planning.domain.plans import resolve_reminder_projection

    zone_name = (
        (replacement.event_zone if replacement is not None else None)
        or draft.event_zone
        or default_zone
    )
    try:
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("event_zone must be a valid IANA time-zone name") from None
    requested_time = replacement.local_time if replacement is not None else draft.local_time
    requested_fold = replacement.fold if replacement is not None else draft.fold
    gap_seconds = 0
    repeated_choice: str | None = None
    if requested_time is not None:
        local = datetime.combine(local_date, requested_time)
        start, gap_seconds, repeated_choice = resolve_recurring_wall_time(local, zone)
        if gap_seconds:
            shifted = local + timedelta(seconds=gap_seconds)
            draft_time = shifted.time()
            date_for_schedule = shifted.date()
        else:
            draft_time = requested_time
            date_for_schedule = local_date
        draft_fold = "earlier" if repeated_choice else requested_fold
    else:
        draft_time = None
        date_for_schedule = local_date
        draft_fold = None
        start = None
    schedule_template = replacement or OneOffPlanDraft(
        kind=draft.kind,
        title=draft.title,
        local_date=local_date,
        local_time=requested_time,
        event_zone=zone_name,
        end_local_date=draft.end_local_date,
        end_local_time=draft.end_local_time,
        duration_seconds=draft.duration_seconds,
        fold=requested_fold,
        end_fold=replacement.end_fold if replacement is not None else draft.end_fold,
        reminders=replacement.reminders if replacement is not None else draft.reminders,
    )
    if schedule_template.kind is not draft.kind:
        raise ValueError("a recurrence exception cannot change the plan kind")
    end_date_offset = (
        (schedule_template.end_local_date or schedule_template.local_date)
        - schedule_template.local_date
        if schedule_template.end_local_time is not None
        else None
    )
    item = replace(
        schedule_template,
        local_date=date_for_schedule,
        local_time=draft_time,
        event_zone=schedule_template.event_zone or zone_name,
        end_local_date=date_for_schedule + end_date_offset if end_date_offset is not None else None,
        fold=draft_fold,
    )
    proposal = propose_plan(
        owner_id,
        item,
        default_zone=default_zone,
        default_reminders=default_reminders,
        now=now,
    )
    # For an adjusted spring-gap time, resolve_schedule now sees a valid shifted
    # wall time. Verify it agrees with the exact-gap resolver above.
    if start is not None and proposal.occurrence.schedule.starts_at != start:
        raise RuntimeError("recurring local-time resolution diverged from Planning schedule")
    offsets = item.reminders if item.reminders is not None else default_reminders
    resolution = resolve_reminder_projection(proposal.occurrence.schedule, offsets or (), now=now)
    occurrence = RecurrenceOccurrence(
        sequence=sequence,
        recurrence_key=f"r1:{sequence}",
        schedule=proposal.occurrence.schedule,
        reminders=tuple((item.offset, item.due_at) for item in resolution.scheduled),
        omitted_reminders=resolution.omitted,
        dst_gap_shift_seconds=gap_seconds,
        repeated_time_choice=repeated_choice,
    )
    return occurrence, gap_seconds, repeated_choice


def resolve_specific_occurrence(
    series: RecurringPlanDraft,
    replacement: OneOffPlanDraft,
    *,
    owner_id: UUID,
    sequence: int,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    now: datetime,
) -> RecurrenceOccurrence:
    """Resolve one approved recurrence exception with the series DST rules."""
    occurrence, _, _ = _resolve_recurring_occurrence(
        series,
        replacement.local_date,
        owner_id=owner_id,
        default_zone=default_zone,
        default_reminders=default_reminders,
        now=now,
        sequence=sequence,
        replacement=replacement,
    )
    return occurrence


def resolve_recurring_wall_time(
    local: datetime, zone: ZoneInfo
) -> tuple[datetime, int, str | None]:
    """Resolve a recurring wall time; gap shifts by its exact size, folds choose earlier."""
    candidates: dict[datetime, datetime] = {}
    offsets = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        offsets.append(aware.utcoffset())
        instant = aware.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates[instant] = aware
    instants = sorted(candidates)
    if instants:
        repeated = len(instants) > 1
        return instants[0], 0, "earlier" if repeated else None
    before, after = offsets
    if before is None or after is None or after <= before:
        raise ScheduleNeedsClarification("recurring local time cannot be resolved")
    gap_seconds = int((after - before).total_seconds())
    shifted = local + timedelta(seconds=gap_seconds)
    instant = shifted.replace(tzinfo=zone).astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != shifted:
        raise ScheduleNeedsClarification(
            "recurring local time falls in an unsupported daylight gap"
        )
    return instant, gap_seconds, None


def recurrence_projection_digest(
    draft: RecurringPlanDraft,
    occurrences: list[RecurrenceOccurrence] | tuple[RecurrenceOccurrence, ...],
) -> str:
    """Hash typed series fields and exact bounded schedule, excluding random ids and time."""
    rule = draft.recurrence
    payload = {
        "schema_version": 1,
        "kind": draft.kind.value,
        "title": draft.title.strip(),
        "start_date": draft.start_date.isoformat(),
        "local_time": draft.local_time.isoformat() if draft.local_time else None,
        "event_zone": draft.event_zone,
        "duration_seconds": draft.duration_seconds,
        "end_local_time": draft.end_local_time.isoformat() if draft.end_local_time else None,
        "end_local_date": draft.end_local_date.isoformat() if draft.end_local_date else None,
        "fold": draft.fold,
        "end_fold": draft.end_fold,
        "reminders": None
        if draft.reminders is None
        else [
            {"kind": offset.kind.value, "amount": offset.amount, "source": offset.source}
            for offset in draft.reminders
        ],
        "recurrence": {
            "frequency": rule.frequency.value,
            "interval": rule.interval,
            "weekdays": sorted(rule.weekdays),
            "monthly_day": rule.monthly_day,
            "last_day": rule.last_day,
            "count": rule.count,
            "end_date": rule.end_date.isoformat() if rule.end_date else None,
        },
        "occurrences": [
            {
                "sequence": occurrence.sequence,
                "key": occurrence.recurrence_key,
                "local_date": occurrence.schedule.local_date.isoformat(),
                "local_time": occurrence.schedule.local_time.isoformat()
                if occurrence.schedule.local_time
                else None,
                "zone": occurrence.schedule.event_zone,
                "starts_at": _utc_iso(occurrence.schedule.starts_at),
                "ends_at": _utc_iso(occurrence.schedule.ends_at),
                "deadline_at": _utc_iso(occurrence.schedule.deadline_at),
                "duration_seconds": occurrence.schedule.duration_seconds,
                "dst_gap_shift_seconds": occurrence.dst_gap_shift_seconds,
                "repeated_time_choice": occurrence.repeated_time_choice,
                "reminders": [
                    {
                        "kind": offset.kind.value,
                        "amount": offset.amount,
                        "source": offset.source,
                        "due_at": _utc_iso(due_at),
                    }
                    for offset, due_at in occurrence.reminders
                ],
                "omitted_reminders": [
                    {
                        "kind": omitted.offset.kind.value,
                        "amount": omitted.offset.amount,
                        "source": omitted.offset.source,
                        "due_at": _utc_iso(omitted.due_at),
                        "reason": omitted.reason,
                    }
                    for omitted in occurrence.omitted_reminders
                ],
                "reminder_notices": [
                    {
                        "kind": notice.kind,
                        "offset_kind": notice.offset.kind.value,
                        "offset_amount": notice.offset.amount,
                        "source": notice.offset.source,
                        "original_due_at": _utc_iso(notice.original_due_at),
                        "due_at": _utc_iso(notice.due_at),
                    }
                    for notice in occurrence.reminder_notices
                ],
            }
            for occurrence in occurrences
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _utc_iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
