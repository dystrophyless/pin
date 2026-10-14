"""Pure notification schedule projection for quiet-hour policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReminderNoticeKind(StrEnum):
    QUIET_DEFERRED = "quiet_deferred"
    QUIET_CONFLICT = "quiet_conflict"


@dataclass(frozen=True, slots=True)
class ReminderCandidate:
    """A calendar/elapsed reminder already resolved by its schedule owner."""

    offset_kind: str
    offset_amount: int
    source: str
    due_at: datetime

    def __post_init__(self) -> None:
        if self.offset_kind not in {"calendar_days", "elapsed_seconds"}:
            raise ValueError("offset_kind must be calendar_days or elapsed_seconds")
        if type(self.offset_amount) is not int or self.offset_amount <= 0:
            raise ValueError("offset_amount must be a positive integer")
        if self.source not in {"default", "explicit"}:
            raise ValueError("source must be default or explicit")
        _require_aware(self.due_at, "due_at")


@dataclass(frozen=True, slots=True)
class OmittedReminder:
    """A previewed reminder that policy deliberately excludes."""

    offset_kind: str
    offset_amount: int
    source: str
    due_at: datetime
    reason: str

    def __post_init__(self) -> None:
        ReminderCandidate(self.offset_kind, self.offset_amount, self.source, self.due_at)
        if not self.reason.strip():
            raise ValueError("an omitted reminder must have a reason")


@dataclass(frozen=True, slots=True)
class ReminderNotice:
    kind: ReminderNoticeKind
    offset_kind: str
    offset_amount: int
    source: str
    original_due_at: datetime
    due_at: datetime

    def __post_init__(self) -> None:
        ReminderCandidate(self.offset_kind, self.offset_amount, self.source, self.original_due_at)
        _require_aware(self.due_at, "due_at")


@dataclass(frozen=True, slots=True)
class ReminderProjection:
    scheduled: tuple[ReminderCandidate, ...]
    omitted: tuple[OmittedReminder, ...]
    notices: tuple[ReminderNotice, ...]


def project_reminders(
    candidates: tuple[ReminderCandidate, ...],
    omitted: tuple[OmittedReminder, ...],
    *,
    event_start_at: datetime,
    now: datetime,
    quiet_zone: str,
    quiet_start: time,
    quiet_end: time,
) -> ReminderProjection:
    """Apply quiet-hour defaults without changing explicitly requested times.

    Calendar-day and elapsed offsets, along with already-past default omissions,
    are supplied by Planning so preview and persistence share one resolution.
    """
    _require_aware(event_start_at, "event_start_at")
    _require_aware(now, "now")
    if quiet_start.tzinfo is not None or quiet_end.tzinfo is not None:
        raise ValueError("quiet-hour bounds must be local wall-clock times")
    if quiet_start == quiet_end:
        raise ValueError("quiet-hour bounds must differ")
    try:
        zone = ZoneInfo(quiet_zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("quiet_zone must be a valid IANA time-zone name") from None

    seen: set[tuple[str, int]] = set()
    scheduled: list[ReminderCandidate] = []
    all_omitted = list(omitted)
    notices: list[ReminderNotice] = []
    for candidate in candidates:
        key = (candidate.offset_kind, candidate.offset_amount)
        if key in seen:
            raise ValueError("reminder offsets must be unique")
        seen.add(key)
        if not _is_quiet(candidate.due_at, zone, quiet_start, quiet_end):
            scheduled.append(candidate)
            continue

        if candidate.source == "explicit":
            scheduled.append(candidate)
            notices.append(
                ReminderNotice(
                    ReminderNoticeKind.QUIET_CONFLICT,
                    candidate.offset_kind,
                    candidate.offset_amount,
                    candidate.source,
                    candidate.due_at.astimezone(UTC),
                    candidate.due_at.astimezone(UTC),
                )
            )
            continue

        deferred = defer_until_quiet_end(candidate.due_at, zone, quiet_start, quiet_end)
        if deferred <= now.astimezone(UTC) or deferred >= event_start_at.astimezone(UTC):
            all_omitted.append(
                OmittedReminder(
                    candidate.offset_kind,
                    candidate.offset_amount,
                    candidate.source,
                    candidate.due_at.astimezone(UTC),
                    "quiet_unusable",
                )
            )
            continue

        scheduled.append(
            ReminderCandidate(
                candidate.offset_kind,
                candidate.offset_amount,
                candidate.source,
                deferred,
            )
        )
        notices.append(
            ReminderNotice(
                ReminderNoticeKind.QUIET_DEFERRED,
                candidate.offset_kind,
                candidate.offset_amount,
                candidate.source,
                candidate.due_at.astimezone(UTC),
                deferred,
            )
        )

    for excluded in omitted:
        key = (excluded.offset_kind, excluded.offset_amount)
        if key in seen:
            raise ValueError("a reminder cannot be both scheduled and omitted")
        seen.add(key)

    return ReminderProjection(tuple(scheduled), tuple(all_omitted), tuple(notices))


def _is_quiet(value: datetime, zone: ZoneInfo, start: time, end: time) -> bool:
    local_time = value.astimezone(zone).time().replace(tzinfo=None)
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def defer_until_quiet_end(value: datetime, zone: ZoneInfo, start: time, end: time) -> datetime:
    """Return the input instant outside quiet hours, or their next valid end."""
    _require_aware(value, "value")
    if start.tzinfo is not None or end.tzinfo is not None:
        raise ValueError("quiet-hour bounds must be local wall-clock times")
    if start == end:
        raise ValueError("quiet-hour bounds must differ")
    instant = value.astimezone(UTC)
    if not _is_quiet(instant, zone, start, end):
        return instant
    return _next_quiet_end(instant, zone, start, end)


def _next_quiet_end(value: datetime, zone: ZoneInfo, start: time, end: time) -> datetime:
    local = value.astimezone(zone)
    local_time = local.time().replace(tzinfo=None)
    end_date: date
    if start < end or local_time < end:
        end_date = local.date()
    else:
        end_date = local.date() + timedelta(days=1)
    for day_offset in range(2):
        wall_end = datetime.combine(end_date + timedelta(days=day_offset), end)
        candidates: set[datetime] = set()
        for fold in (0, 1):
            candidate = wall_end.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) == wall_end:
                candidates.add(candidate)

        future_candidates = tuple(candidate for candidate in candidates if candidate > value)
        if future_candidates:
            # Keep the whole repeated wall-clock interval quiet through the second fold.
            return max(future_candidates)

        if not candidates:
            # Fold 0 preserves the existing forward-gap interpretation for nonexistent
            # local quiet ends (for example, 02:30 becoming 03:30 at spring-forward).
            gap_candidate = wall_end.replace(tzinfo=zone, fold=0).astimezone(UTC)
            if gap_candidate > value:
                return gap_candidate
    raise ValueError("could not resolve a future quiet-hour end")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
