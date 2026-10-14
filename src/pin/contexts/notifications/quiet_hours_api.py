"""Published quiet-hour boundary helper for contexts that schedule delivery."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pin.contexts.notifications.domain.reminders import defer_until_quiet_end


def defer_until_quiet_hours_end(
    value: datetime,
    *,
    zone_name: str,
    quiet_start: time,
    quiet_end: time,
) -> datetime:
    """Return ``value`` if it is outside quiet hours, else the next quiet end."""
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("quiet_zone must be a valid IANA time-zone name") from None
    return defer_until_quiet_end(value, zone, quiet_start, quiet_end)


__all__ = ["defer_until_quiet_hours_end"]
