"""Injectable UTC clock and the adopted due-work polling interval."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

DUE_POLL_INTERVAL_SECONDS = 1.0


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

