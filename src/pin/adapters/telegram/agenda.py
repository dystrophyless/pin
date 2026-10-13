"""Agenda command and callback adapter for the private Telegram worker."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.presentation.agenda import (
    AgendaReply,
    render_agenda,
    render_agenda_expired,
    render_agenda_unavailable,
)
from pin.adapters.telegram.presentation.callbacks import CallbackBinding
from pin.contexts.billing.api import CheckAccess, Operation, SqlAlchemyAccessUsageStore
from pin.contexts.identity.api import PreferencesSnapshot
from pin.contexts.planning.api import (
    AgendaView,
    open_agenda,
    read_agenda_page,
    remember_page_start,
)
from pin.contexts.planning.infrastructure.agenda import SqlAlchemyAgendaReadStore


def agenda_command(text: str) -> AgendaView | None:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    command = first.split("@", maxsplit=1)[0].casefold()
    return {"/today": AgendaView.TODAY, "/week": AgendaView.WEEK}.get(command)


async def handle_agenda_command(
    session: AsyncSession,
    *,
    owner_id: UUID,
    preferences: PreferencesSnapshot,
    view: AgendaView,
    now: datetime,
) -> AgendaReply:
    access = await _check_agenda_access(session, owner_id=owner_id, now=now)
    if not access:
        return render_agenda_unavailable(language=preferences.language)
    page = await open_agenda(
        SqlAlchemyAgendaReadStore(session),
        owner_id=owner_id,
        view=view,
        display_zone=preferences.home_zone,
        now=now,
    )
    return render_agenda(page, language=preferences.language)


async def handle_agenda_callback(
    session: AsyncSession,
    *,
    owner_id: UUID,
    preferences: PreferencesSnapshot,
    binding: CallbackBinding,
    now: datetime,
) -> AgendaReply | None:
    if binding.action not in {"agenda_page", "agenda_completed"}:
        return None
    access = await _check_agenda_access(session, owner_id=owner_id, now=now)
    if not access:
        return render_agenda_unavailable(language=preferences.language)
    store = SqlAlchemyAgendaReadStore(session)
    if binding.action == "agenda_page":
        snapshot_text, separator, offset_text = binding.target_id.partition(":")
        if not separator:
            return render_agenda_expired(language=preferences.language)
        try:
            snapshot_id = UUID(snapshot_text)
            offset = int(offset_text)
        except ValueError:
            return render_agenda_expired(language=preferences.language)
        page = await read_agenda_page(
            store,
            owner_id=owner_id,
            snapshot_id=snapshot_id,
            start_index=offset,
            now=now,
        )
        return (
            render_agenda(page, language=preferences.language)
            if page is not None
            else render_agenda_expired(language=preferences.language)
        )
    if binding.action == "agenda_completed":
        view_text, separator, enabled_text = binding.target_id.partition("|")
        if not separator or enabled_text not in {"0", "1"}:
            return render_agenda_expired(language=preferences.language)
        try:
            view = AgendaView(view_text)
        except ValueError:
            return render_agenda_expired(language=preferences.language)
        page = await open_agenda(
            store,
            owner_id=owner_id,
            view=view,
            display_zone=preferences.home_zone,
            include_completed=enabled_text == "1",
            now=now,
        )
        return render_agenda(page, language=preferences.language)
    return None


async def _check_agenda_access(session: AsyncSession, *, owner_id: UUID, now: datetime) -> bool:
    decision = await SqlAlchemyAccessUsageStore(session).check_access(
        CheckAccess(owner_id, Operation.VIEW_PLAN, now)
    )
    return decision.allowed


async def remember_agenda_navigation(
    session: AsyncSession,
    *,
    owner_id: UUID,
    action: str,
    target_id: str,
) -> None:
    if action != "agenda_page":
        return
    snapshot_text, separator, offset_text = target_id.partition(":")
    if not separator:
        return
    try:
        snapshot_id = UUID(snapshot_text)
        offset = int(offset_text)
    except ValueError:
        return
    await remember_page_start(
        SqlAlchemyAgendaReadStore(session),
        owner_id=owner_id,
        snapshot_id=snapshot_id,
        start_index=offset,
    )
