"""Framework-independent reminder delivery outcomes and message rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DeliveryOutcomeKind(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    DEFERRED = "deferred"
    COALESCED = "coalesced"


@dataclass(frozen=True, slots=True)
class DeliveryAttemptOutcome:
    intent_id: UUID | None
    owner_id: UUID
    dispatch_version: int
    target_id: int | None
    attempt_id: UUID
    outcome: DeliveryOutcomeKind
    occurred_at: datetime
    failure_code: str | None = None
    check_in_cycle_id: UUID | None = None
    access_period_id: str | None = None
    reply_event_id: UUID | None = None
    quiet_conflict: bool = False
    recovery_episode_key: str | None = None


@dataclass(frozen=True, slots=True)
class ReminderMessage:
    destination_id: int
    text: str


@dataclass(frozen=True, slots=True)
class RecoverySummaryItem:
    title: str
    due_label: str


def render_recovery_summary(
    items: tuple[RecoverySummaryItem, ...], language: str
) -> str:
    """Describe current unresolved plan rows without implying a check-in campaign."""
    if not items:
        raise ValueError("recovery summary needs at least one current item")
    if language not in {"en", "ru"}:
        raise ValueError("recovery summary language must be ru or en")
    visible = items[:10]
    if language == "ru":
        lines = ["После перерыва остались незавершённые дела:"]
    else:
        lines = ["These items are still unfinished after the interruption:"]
    lines.extend(f"• {item.title.strip()} — {item.due_label}" for item in visible)
    if len(items) > len(visible):
        remaining = len(items) - len(visible)
        lines.append(
            f"И ещё {remaining}." if language == "ru" else f"And {remaining} more."
        )
    return "\n".join(lines)[:3500]


def render_reminder(title: str, language: str) -> str:
    """Render a plain-text reminder with untrusted title content left unparsed."""
    normalized = title.strip()
    if not normalized:
        raise ValueError("reminder title cannot be empty")
    if language == "ru":
        return f"Напоминание: {normalized}"
    if language == "en":
        return f"Reminder: {normalized}"
    raise ValueError("reminder language must be ru or en")
