"""Published reminder-intent contracts and operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, time
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pin.contexts.notifications.application.access_resume import resumable_access_occurrences
from pin.contexts.notifications.application.contracts import (
    AccessResumeOccurrence,
    AccessResumeReminder,
    AccessResumeStore,
    CurrentOccurrenceSchedule,
    CurrentPlanSchedule,
    CurrentScheduleStore,
    ReconciliationResult,
    ReminderIntentStore,
    ResumeAfterAccess,
    ResumeAfterAccessResult,
    ScheduleChange,
)
from pin.contexts.notifications.application.delivery import (
    ACCOUNTABILITY_DELIVERY_JOB_KIND,
    AccountabilityTransport,
    KnownDeliveryFailure,
    RecoverySummaryRequest,
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)
from pin.contexts.notifications.application.reconcile import reconcile_schedule_change
from pin.contexts.notifications.domain.delivery import (
    DeliveryAttemptOutcome,
    DeliveryOutcomeKind,
    RecoverySummaryItem,
    render_recovery_summary,
)
from pin.contexts.notifications.domain.reminders import (
    OmittedReminder,
    ReminderCandidate,
    ReminderNotice,
    ReminderNoticeKind,
    ReminderProjection,
    defer_until_quiet_end,
    project_reminders,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from pin.contexts.accountability.api import (
        AccountabilitySend,
        CurrentOccurrenceReader,
        ReviewProfile,
        TodayUntimedReader,
    )
    from pin.platform.jobs import JobClaim


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


async def resume_after_access(
    command: ResumeAfterAccess, *, store: AccessResumeStore
) -> ResumeAfterAccessResult:
    """Rearm only still-current unsent reminder intents after access returns."""
    return await store.resume_after_access(command)


async def deliver_accountability_job(
    sessions: async_sessionmaker[AsyncSession],
    claim: JobClaim,
    transport: AccountabilityTransport,
    *,
    profile_reader: Callable[[AsyncSession, UUID], Awaitable[ReviewProfile | None]],
    current_occurrences_factory: Callable[[AsyncSession], CurrentOccurrenceReader],
    today_untimed_factory: Callable[[AsyncSession], TodayUntimedReader],
    render: Callable[[AsyncSession, AccountabilitySend, datetime], Awaitable[TelegramReplySend]],
    now: Callable[[], datetime],
) -> DeliveryOutcomeKind | None:
    """Load the infrastructure worker only when composition dispatches this job."""
    from pin.contexts.notifications.infrastructure.accountability_delivery import (
        deliver_accountability_job as deliver,
    )

    return await deliver(
        sessions,
        claim,
        transport,
        profile_reader=profile_reader,
        current_occurrences_factory=current_occurrences_factory,
        today_untimed_factory=today_untimed_factory,
        render=render,
        now=now,
    )


__all__ = [
    "AccessResumeOccurrence",
    "AccessResumeReminder",
    "AccessResumeStore",
    "DeliveryAttemptOutcome",
    "ACCOUNTABILITY_DELIVERY_JOB_KIND",
    "AccountabilityTransport",
    "DeliveryOutcomeKind",
    "defer_until_quiet_hours_end",
    "KnownDeliveryFailure",
    "ReminderSend",
    "TelegramReplySend",
    "UnknownDeliveryFailure",
    "CurrentOccurrenceSchedule",
    "CurrentPlanSchedule",
    "CurrentScheduleStore",
    "OmittedReminder",
    "ReminderCandidate",
    "ReminderIntentStore",
    "ReminderNotice",
    "ReminderNoticeKind",
    "ReminderProjection",
    "ReconciliationResult",
    "RecoverySummaryItem",
    "RecoverySummaryRequest",
    "ResumeAfterAccess",
    "ResumeAfterAccessResult",
    "ScheduleChange",
    "project_reminders",
    "reconcile_schedule_change",
    "resumable_access_occurrences",
    "resume_after_access",
    "render_recovery_summary",
    "deliver_accountability_job",
]
