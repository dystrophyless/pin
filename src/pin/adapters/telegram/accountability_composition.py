"""Bind producer-owned reads to Accountability in the current delivery transaction."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.accountability import render_accountability_delivery
from pin.contexts.accountability.api import (
    AccountabilitySend,
    CurrentOccurrence,
    OpenUntimedItem,
    ReviewProfile,
    consume_delivery_outcomes,
    consume_planning_cycle_events,
    schedule_due_accountability,
)
from pin.contexts.identity.api import list_review_profiles
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.contexts.notifications.application.delivery import TelegramReplySend
from pin.contexts.notifications.infrastructure.delivery_worker import ReminderTransport
from pin.contexts.planning.api import (
    get_accountability_occurrence,
    list_today_open_untimed,
)
from pin.contexts.planning.application.accountability_reads import AccountabilityOccurrence


def _current(item: AccountabilityOccurrence) -> CurrentOccurrence:
    return CurrentOccurrence(
        owner_id=item.owner_id,
        occurrence_id=item.occurrence_id,
        occurrence_version=item.occurrence_version,
        kind=item.kind,
        state=item.state,
        title=item.title,
        event_zone=item.event_zone,
        local_date=item.local_date,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        deadline_at=item.deadline_at,
    )


class PlanningAccountabilityReads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current_occurrence(
        self, owner_id: UUID, occurrence_id: UUID, *, for_update: bool = False
    ) -> CurrentOccurrence | None:
        item = await get_accountability_occurrence(
            self.session,
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            for_update=for_update,
        )
        return _current(item) if item is not None else None

    async def list_today_open_untimed(
        self, owner_id: UUID, *, profile_zone: str, at: datetime, for_update: bool = False
    ) -> tuple[OpenUntimedItem, ...]:
        items: list[OpenUntimedItem] = []
        cursor: UUID | None = None
        while True:
            page = await list_today_open_untimed(
                self.session,
                owner_id=owner_id,
                profile_zone=profile_zone,
                at=at,
                after_occurrence_id=cursor,
                for_update=for_update,
            )
            items.extend(
                OpenUntimedItem(item.occurrence_id, item.title, item.local_date)
                for item in page.items
            )
            if page.next_occurrence_id is None:
                return tuple(items)
            cursor = page.next_occurrence_id


async def read_review_profile(
    session: AsyncSession,
    owner_id: UUID,
    transport: ReminderTransport,
    *,
    for_update: bool = True,
) -> ReviewProfile | None:
    identity = SqlAlchemyIdentityStore(session)
    read_preferences = identity.lock_preferences if for_update else identity.get_preferences
    profile = await read_preferences(owner_id)
    if profile is None:
        return None
    destination = await transport.resolve_destination(session, owner_id)
    if destination is None:
        return None
    return ReviewProfile(
        owner_id=owner_id,
        home_zone=profile.home_zone,
        language=profile.language,
        review_time=profile.review_time,
        quiet_start=profile.quiet_start,
        quiet_end=profile.quiet_end,
        destination_id=destination,
    )


async def schedule_accountability_iteration(
    session: AsyncSession, transport: ReminderTransport, *, at: datetime
) -> None:
    reads = PlanningAccountabilityReads(session)
    await consume_planning_cycle_events(session, current_occurrences=reads, at=at)
    await consume_delivery_outcomes(session, at=at)
    cursor: UUID | None = None
    while True:
        page = await list_review_profiles(session, after_owner_id=cursor)
        profiles: list[ReviewProfile] = []
        for item in page.items:
            profile = await read_review_profile(
                session, item.owner_id, transport, for_update=False
            )
            if profile is not None:
                profiles.append(profile)
        if profiles:
            await schedule_due_accountability(
                session, profiles=tuple(profiles), today_untimed=reads, now=at
            )
        if page.next_owner_id is None:
            return
        cursor = page.next_owner_id


async def render_accountability_message(
    session: AsyncSession, send: AccountabilitySend, at: datetime
) -> TelegramReplySend:
    reads = PlanningAccountabilityReads(session)
    occurrences: list[CurrentOccurrence] = []
    for occurrence_id in send.occurrence_ids:
        current = await reads.get_current_occurrence(send.owner_id, occurrence_id)
        if current is not None:
            occurrences.append(current)
    text, keyboard = await render_accountability_delivery(
        session, send, occurrences=tuple(occurrences), now=at
    )
    return TelegramReplySend(
        destination_id=send.destination_id,
        markdown=text,
        plain_text=text,
        reply_markup=keyboard,
        parse_mode=None,
    )
