"""Confirmed language, home-zone, and reminder preferences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PreferenceVersionConflict(RuntimeError):
    """The profile changed after this preference preview was prepared."""


def validate_home_zone(value: str) -> str:
    zone = value.strip()
    if not zone or zone.startswith("/") or ".." in zone.split("/"):
        raise ValueError("enter an IANA time-zone name")
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("enter a valid IANA time-zone name") from error
    return zone


@dataclass(frozen=True, slots=True)
class Preferences:
    language: str
    home_zone: str
    reminder_calendar_days: int = 1
    reminder_elapsed_hours: int = 1
    quiet_start: time = time(22, 0)
    quiet_end: time = time(8, 0)
    review_time: time = time(20, 0)
    version: int = 1

    def __post_init__(self) -> None:
        if self.language not in {"ru", "en"}:
            raise ValueError("language must be ru or en")
        if validate_home_zone(self.home_zone) != self.home_zone:
            raise ValueError("home_zone must be normalized")
        for name, value in (
            ("reminder_calendar_days", self.reminder_calendar_days),
            ("reminder_elapsed_hours", self.reminder_elapsed_hours),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.quiet_start == self.quiet_end:
            raise ValueError("quiet hours must have a non-empty interval")
        if self.version < 1:
            raise ValueError("version must be positive")

    def quiet_contains(self, value: time) -> bool:
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= value < self.quiet_end
        return value >= self.quiet_start or value < self.quiet_end


def in_quiet_hours(value: time, *, start: time, end: time) -> bool:
    """Return whether a local wall time falls in a possibly overnight interval."""
    if start == end:
        raise ValueError("quiet hours must have a non-empty interval")
    if start < end:
        return start <= value < end
    return value >= start or value < end
