"""Pure one-off planning rules and IANA wall-clock resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PlanKind(StrEnum):
    APPOINTMENT = "appointment"
    TASK = "task"
    DEADLINE = "deadline"
    UNTIMED = "untimed"


class OffsetKind(StrEnum):
    CALENDAR_DAYS = "calendar_days"
    ELAPSED_SECONDS = "elapsed_seconds"


class OccurrenceState(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScheduleNeedsClarification(ValueError):
    """The requested calendar intent cannot be applied without a user choice."""


class PlanVersionConflict(ValueError):
    """The occurrence changed after the caller's preview."""


@dataclass(frozen=True, slots=True)
class ReminderOffset:
    kind: OffsetKind
    amount: int
    source: str = "explicit"

    def __post_init__(self) -> None:
        if type(self.amount) is not int or not 0 < self.amount <= 2_147_483_647:
            raise ValueError("reminder offsets must be positive integers")
        if self.source not in {"explicit", "default"}:
            raise ValueError("reminder source must be explicit or default")


@dataclass(frozen=True, slots=True)
class OneOffPlanDraft:
    kind: PlanKind
    title: str
    local_date: date
    local_time: time | None = None
    event_zone: str | None = None
    end_local_date: date | None = None
    end_local_time: time | None = None
    duration_seconds: int | None = None
    fold: str | None = None
    end_fold: str | None = None
    reminders: tuple[ReminderOffset, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PlanKind):
            raise ValueError("kind must be a supported PlanKind")
        if type(self.local_date) is not date:
            raise ValueError("local_date must be a calendar date")
        if self.end_local_date is not None and type(self.end_local_date) is not date:
            raise ValueError("end_local_date must be a calendar date")
        if not self.title.strip() or len(self.title) > 500:
            raise ValueError("title must contain 1 to 500 characters")
        if self.fold not in {None, "earlier", "later"}:
            raise ValueError("fold must be earlier or later")
        if self.end_fold not in {None, "earlier", "later"}:
            raise ValueError("end_fold must be earlier or later")
        if self.local_time is not None and self.local_time.tzinfo is not None:
            raise ValueError("local_time must be a wall-clock time without a zone")
        if self.end_local_time is not None and self.end_local_time.tzinfo is not None:
            raise ValueError("end_local_time must be a wall-clock time without a zone")
        if self.end_local_date is not None and self.end_local_time is None:
            raise ValueError("end_local_date requires an explicit appointment end time")
        if self.duration_seconds is not None and (
            type(self.duration_seconds) is not int or not 0 < self.duration_seconds <= 2_147_483_647
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if self.kind is PlanKind.UNTIMED:
            if any(
                value is not None
                for value in (
                    self.local_time,
                    self.end_local_date,
                    self.end_local_time,
                    self.duration_seconds,
                )
            ):
                raise ValueError("untimed plans cannot contain a time or duration")
            if self.fold is not None or self.end_fold is not None:
                raise ValueError("untimed plans cannot contain a DST fold")
            if self.reminders:
                raise ValueError("untimed plans cannot have timed reminders")
        elif self.local_time is None:
            raise ValueError("timed plans require an explicit local time")
        if self.kind is PlanKind.APPOINTMENT:
            if (self.end_local_time is None) == (self.duration_seconds is None):
                raise ValueError("appointments require exactly one explicit end or duration")
        elif (
            self.end_local_date is not None
            or self.end_local_time is not None
            or self.end_fold is not None
        ):
            raise ValueError("only appointments may specify an end time")
        if self.kind is PlanKind.DEADLINE and self.duration_seconds is not None:
            raise ValueError("deadlines do not have an appointment duration")
        if self.reminders is not None and len({(r.kind, r.amount) for r in self.reminders}) != len(
            self.reminders
        ):
            raise ValueError("reminder offsets must be unique")


@dataclass(frozen=True, slots=True)
class ResolvedSchedule:
    kind: PlanKind
    event_zone: str
    local_date: date
    local_time: time | None
    fold: str | None
    end_local_date: date | None
    end_local_time: time | None
    end_fold: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    deadline_at: datetime | None
    duration_seconds: int | None

    @property
    def anchor_at(self) -> datetime | None:
        return self.deadline_at or self.starts_at


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    offset: ReminderOffset
    due_at: datetime


@dataclass(frozen=True, slots=True)
class ReminderOmission:
    offset: ReminderOffset
    due_at: datetime
    reason: str = "past"


@dataclass(frozen=True, slots=True)
class ReminderResolution:
    scheduled: tuple[ReminderSchedule, ...]
    omitted: tuple[ReminderOmission, ...]


@dataclass(frozen=True, slots=True)
class PlanItem:
    id: UUID
    owner_id: UUID
    kind: PlanKind
    title: str
    version: int


@dataclass(frozen=True, slots=True)
class Occurrence:
    id: UUID
    owner_id: UUID
    plan_item_id: UUID
    schedule: ResolvedSchedule
    version: int = 1
    state: OccurrenceState = OccurrenceState.OPEN

    def reschedule(self, schedule: ResolvedSchedule, expected_version: int) -> Occurrence:
        if self.version != expected_version:
            raise PlanVersionConflict("occurrence changed after preview")
        if schedule.kind is not self.schedule.kind:
            raise ValueError("a one-off reschedule cannot change the plan kind")
        return replace(self, schedule=schedule, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class ProposedPlan:
    item: PlanItem
    occurrence: Occurrence
    reminders: tuple[ReminderSchedule, ...]
    omitted_reminders: tuple[ReminderOmission, ...] = ()


def propose_plan(
    owner_id: UUID,
    draft: OneOffPlanDraft,
    *,
    default_zone: str,
    default_reminders: tuple[ReminderOffset, ...],
    now: datetime,
) -> ProposedPlan:
    schedule = resolve_schedule(draft, default_zone=default_zone, now=now)
    offsets = (
        ()
        if draft.kind is PlanKind.UNTIMED
        else draft.reminders
        if draft.reminders is not None
        else default_reminders
    )
    reminder_resolution = resolve_reminder_projection(schedule, offsets, now=now)
    item_id, occurrence_id = uuid4(), uuid4()
    item = PlanItem(item_id, owner_id, draft.kind, draft.title.strip(), 1)
    occurrence = Occurrence(occurrence_id, owner_id, item_id, schedule)
    return ProposedPlan(
        item, occurrence, reminder_resolution.scheduled, reminder_resolution.omitted
    )


def resolve_schedule(
    draft: OneOffPlanDraft,
    *,
    default_zone: str,
    now: datetime,
) -> ResolvedSchedule:
    _require_aware(now)
    zone_name = draft.event_zone or default_zone
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("event_zone must be a valid IANA time-zone name") from None
    local = datetime.combine(draft.local_date, draft.local_time) if draft.local_time else None
    if local is None:
        if draft.local_date < now.astimezone(zone).date():
            raise ScheduleNeedsClarification("an untimed plan cannot target a past local date")
        return ResolvedSchedule(
            kind=draft.kind,
            event_zone=zone_name,
            local_date=draft.local_date,
            local_time=None,
            fold=None,
            end_local_date=None,
            end_local_time=None,
            end_fold=None,
            starts_at=None,
            ends_at=None,
            deadline_at=None,
            duration_seconds=None,
        )

    start = _resolve_local(local, zone, draft.fold, "scheduled time")
    if start <= now.astimezone(UTC):
        raise ScheduleNeedsClarification("the requested local time is in the past")
    end: datetime | None = None
    duration = draft.duration_seconds
    if draft.end_local_time is not None:
        end_date = draft.end_local_date or draft.local_date
        end_local = datetime.combine(end_date, draft.end_local_time)
        end = _resolve_local(end_local, zone, draft.end_fold, "appointment end")
        if end <= start:
            raise ValueError("appointment end must be after its start")
        duration = int((end - start).total_seconds())
    elif duration is not None:
        end = start + timedelta(seconds=duration)

    return ResolvedSchedule(
        kind=draft.kind,
        event_zone=zone_name,
        local_date=draft.local_date,
        local_time=draft.local_time,
        fold=draft.fold,
        end_local_date=(draft.end_local_date or draft.local_date)
        if draft.end_local_time is not None
        else None,
        end_local_time=draft.end_local_time,
        end_fold=draft.end_fold,
        starts_at=start if draft.kind in {PlanKind.APPOINTMENT, PlanKind.TASK} else None,
        ends_at=end if draft.kind is PlanKind.APPOINTMENT else None,
        deadline_at=start if draft.kind is PlanKind.DEADLINE else None,
        duration_seconds=duration if draft.kind in {PlanKind.APPOINTMENT, PlanKind.TASK} else None,
    )


def resolve_reminders(
    schedule: ResolvedSchedule,
    offsets: tuple[ReminderOffset, ...],
    *,
    now: datetime,
) -> tuple[ReminderSchedule, ...]:
    return resolve_reminder_projection(schedule, offsets, now=now).scheduled


def resolve_reminder_projection(
    schedule: ResolvedSchedule,
    offsets: tuple[ReminderOffset, ...],
    *,
    now: datetime,
) -> ReminderResolution:
    _require_aware(now)
    anchor = schedule.anchor_at
    if anchor is None:
        if offsets:
            raise ValueError("untimed plans cannot have reminders")
        return ReminderResolution((), ())
    zone = ZoneInfo(schedule.event_zone)
    resolved: list[ReminderSchedule] = []
    omitted: list[ReminderOmission] = []
    for offset in offsets:
        if offset.kind is OffsetKind.ELAPSED_SECONDS:
            due_at = anchor - timedelta(seconds=offset.amount)
        else:
            local_anchor = anchor.astimezone(zone).replace(tzinfo=None)
            reminder_local = local_anchor - timedelta(days=offset.amount)
            due_at = _resolve_local(reminder_local, zone, None, "calendar reminder")
        if due_at <= now.astimezone(UTC):
            if offset.source == "default":
                omitted.append(ReminderOmission(offset, due_at.astimezone(UTC)))
                continue
            raise ScheduleNeedsClarification("an explicitly requested reminder is in the past")
        resolved.append(ReminderSchedule(offset, due_at))
    return ReminderResolution(tuple(resolved), tuple(omitted))


def payload_digest(
    drafts: tuple[OneOffPlanDraft, ...], *, expected_preferences_version: int
) -> str:
    """Hash the exact typed fields that a trusted preview producer approved."""
    data = {
        "schema_version": 1,
        "expected_preferences_version": expected_preferences_version,
        "plans": [
            {
                "kind": draft.kind.value,
                "title": draft.title,
                "local_date": draft.local_date.isoformat(),
                "local_time": draft.local_time.isoformat() if draft.local_time else None,
                "event_zone": draft.event_zone,
                "end_local_date": draft.end_local_date.isoformat()
                if draft.end_local_date
                else None,
                "end_local_time": draft.end_local_time.isoformat()
                if draft.end_local_time
                else None,
                "duration_seconds": draft.duration_seconds,
                "fold": draft.fold,
                "end_fold": draft.end_fold,
                "reminders": (
                    None
                    if draft.reminders is None
                    else [
                        {
                            "kind": offset.kind.value,
                            "amount": offset.amount,
                            "source": offset.source,
                        }
                        for offset in draft.reminders
                    ]
                ),
            }
            for draft in drafts
        ],
    }
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def approval_projection_digest(
    drafts: tuple[OneOffPlanDraft, ...],
    *,
    expected_preferences_version: int,
    plans: tuple[ProposedPlan, ...],
) -> str:
    """Fingerprint approved typed fields and their exact resolved schedule/reminders.

    Generated item/occurrence identifiers and processing time are deliberately
    excluded so the same approved calendar intent hashes identically on replay.
    """
    if len(drafts) != len(plans):
        raise ValueError("draft and proposal counts must match")
    projection = []
    for plan in plans:
        schedule = plan.occurrence.schedule
        projection.append(
            {
                "schedule": {
                    "kind": schedule.kind.value,
                    "event_zone": schedule.event_zone,
                    "local_date": schedule.local_date.isoformat(),
                    "local_time": schedule.local_time.isoformat() if schedule.local_time else None,
                    "fold": schedule.fold,
                    "end_local_date": schedule.end_local_date.isoformat()
                    if schedule.end_local_date
                    else None,
                    "end_local_time": schedule.end_local_time.isoformat()
                    if schedule.end_local_time
                    else None,
                    "end_fold": schedule.end_fold,
                    "starts_at": _utc_iso(schedule.starts_at),
                    "ends_at": _utc_iso(schedule.ends_at),
                    "deadline_at": _utc_iso(schedule.deadline_at),
                    "duration_seconds": schedule.duration_seconds,
                },
                "reminders": [
                    {
                        "kind": reminder.offset.kind.value,
                        "amount": reminder.offset.amount,
                        "source": reminder.offset.source,
                        "due_at": _utc_iso(reminder.due_at),
                        "status": "scheduled",
                    }
                    for reminder in plan.reminders
                ],
                "omitted_reminders": [
                    {
                        "kind": omitted.offset.kind.value,
                        "amount": omitted.offset.amount,
                        "source": omitted.offset.source,
                        "due_at": _utc_iso(omitted.due_at),
                        "reason": omitted.reason,
                    }
                    for omitted in plan.omitted_reminders
                ],
            }
        )
    encoded = json.dumps(
        {
            "schema_version": 1,
            "typed_payload_sha256": payload_digest(
                drafts, expected_preferences_version=expected_preferences_version
            ),
            "plans": projection,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _utc_iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _resolve_local(
    local: datetime, zone: ZoneInfo, choice: str | None, description: str
) -> datetime:
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        instant = aware.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates[instant] = aware
    if not candidates:
        raise ScheduleNeedsClarification(f"{description} falls in a daylight-saving gap")
    instants = sorted(candidates)
    if len(instants) > 1 and choice is None:
        raise ScheduleNeedsClarification(f"{description} repeats; choose earlier or later")
    if choice is not None and choice not in {"earlier", "later"}:
        raise ValueError("ambiguous local times require earlier or later")
    selected = instants[0] if choice != "later" else instants[-1]
    return selected


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
