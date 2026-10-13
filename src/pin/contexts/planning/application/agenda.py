"""Owner-scoped read use cases for the saved calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

SNAPSHOT_TTL = timedelta(hours=24)


class AgendaView(StrEnum):
    TODAY = "today"
    WEEK = "week"


class AgendaSection(StrEnum):
    OVERDUE = "overdue"
    SCHEDULED = "scheduled"
    UNTIMED = "untimed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class AgendaWindow:
    view: AgendaView
    display_zone: str
    start_date: date
    end_date: date
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class AgendaRecord:
    occurrence_id: UUID
    owner_id: UUID
    kind: str
    title: str
    state: str
    event_zone: str
    local_date: date
    local_time: time | None
    end_local_date: date | None
    end_local_time: time | None
    starts_at: datetime | None
    ends_at: datetime | None
    deadline_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgendaSnapshot:
    snapshot_id: UUID
    owner_id: UUID
    window: AgendaWindow
    include_completed: bool
    item_ids: tuple[str, ...]
    page_starts: tuple[int, ...]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AgendaEntry:
    occurrence_id: UUID
    snapshot_index: int
    section: AgendaSection
    record: AgendaRecord
    display_date: date


@dataclass(frozen=True, slots=True)
class AgendaPage:
    snapshot: AgendaSnapshot
    start_index: int
    previous_index: int | None
    entries: tuple[AgendaEntry, ...]


class AgendaReadStore(Protocol):
    async def list_candidates(
        self,
        owner_id: UUID,
        *,
        window: AgendaWindow,
        include_completed: bool,
    ) -> tuple[AgendaRecord, ...]: ...

    async def save_snapshot(self, snapshot: AgendaSnapshot) -> None: ...

    async def get_snapshot(
        self, owner_id: UUID, snapshot_id: UUID, *, now: datetime
    ) -> AgendaSnapshot | None: ...

    async def list_snapshot_records(
        self, owner_id: UUID, occurrence_ids: tuple[UUID, ...]
    ) -> tuple[AgendaRecord, ...]: ...

    async def remember_page_start(
        self, owner_id: UUID, snapshot_id: UUID, *, start_index: int
    ) -> None: ...


def agenda_window(view: AgendaView, display_zone: str, now: datetime) -> AgendaWindow:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("agenda clock must be timezone-aware")
    zone = ZoneInfo(display_zone)
    local_now = now.astimezone(zone)
    today = local_now.date()
    start_day = today if view is AgendaView.TODAY else today - timedelta(days=today.weekday())
    days = 1 if view is AgendaView.TODAY else 7
    end_day = start_day + timedelta(days=days)
    starts_at = datetime.combine(start_day, time.min, zone).astimezone(UTC)
    ends_at = datetime.combine(end_day, time.min, zone).astimezone(UTC)
    return AgendaWindow(view, display_zone, start_day, end_day, starts_at, ends_at)


async def open_agenda(
    store: AgendaReadStore,
    *,
    owner_id: UUID,
    view: AgendaView,
    display_zone: str,
    include_completed: bool = False,
    now: datetime,
) -> AgendaPage:
    window = agenda_window(view, display_zone, now)
    records = await store.list_candidates(
        owner_id, window=window, include_completed=include_completed
    )
    selected = _select_records(records, window=window, include_completed=include_completed, now=now)
    ordered = sorted(selected, key=lambda entry: _order_key(entry, window, now))
    snapshot = AgendaSnapshot(
        snapshot_id=uuid4(),
        owner_id=owner_id,
        window=window,
        include_completed=include_completed,
        item_ids=tuple(str(entry.occurrence_id) for entry in ordered),
        page_starts=(0,),
        created_at=now.astimezone(UTC),
        expires_at=now.astimezone(UTC) + SNAPSHOT_TTL,
    )
    await store.save_snapshot(snapshot)
    return await _page(store, snapshot, start_index=0, now=now)


async def read_agenda_page(
    store: AgendaReadStore,
    *,
    owner_id: UUID,
    snapshot_id: UUID,
    start_index: int,
    now: datetime,
) -> AgendaPage | None:
    snapshot = await store.get_snapshot(owner_id, snapshot_id, now=now)
    if snapshot is None or start_index < 0 or start_index >= max(1, len(snapshot.item_ids)):
        return None
    return await _page(store, snapshot, start_index=start_index, now=now)


async def remember_page_start(
    store: AgendaReadStore,
    *,
    owner_id: UUID,
    snapshot_id: UUID,
    start_index: int,
) -> None:
    await store.remember_page_start(owner_id, snapshot_id, start_index=start_index)


async def _page(
    store: AgendaReadStore,
    snapshot: AgendaSnapshot,
    *,
    start_index: int,
    now: datetime,
) -> AgendaPage:
    ids = tuple(UUID(value) for value in snapshot.item_ids)
    current = await store.list_snapshot_records(snapshot.owner_id, ids)
    selected = {
        entry.occurrence_id: entry
        for entry in _select_records(
            current,
            window=snapshot.window,
            include_completed=snapshot.include_completed,
            now=now,
        )
    }
    page: list[AgendaEntry] = []
    today = now.astimezone(ZoneInfo(snapshot.window.display_zone)).date()
    for index in range(start_index, len(snapshot.item_ids)):
        occurrence_id = UUID(snapshot.item_ids[index])
        entry = selected.get(occurrence_id)
        if entry is None:
            continue
        display_date = _display_date(entry, snapshot.window.display_zone)
        section = _section(entry, today, now)
        page.append(AgendaEntry(occurrence_id, index, section, entry, display_date))
    previous = max((value for value in snapshot.page_starts if value < start_index), default=None)
    return AgendaPage(snapshot, start_index, previous, tuple(page))


def _select_records(
    records: tuple[AgendaRecord, ...],
    *,
    window: AgendaWindow,
    include_completed: bool,
    now: datetime,
) -> tuple[AgendaRecord, ...]:
    selected: list[AgendaRecord] = []
    for record in records:
        if record.state == "cancelled" or record.state == "completed" and not include_completed:
            continue
        if (
            _is_in_window(record, window)
            or record.state == "open"
            and _is_overdue(record, window, now)
        ):
            selected.append(record)
    return tuple(selected)


def _is_in_window(record: AgendaRecord, window: AgendaWindow) -> bool:
    if record.kind == "untimed":
        return window.start_date <= record.local_date < window.end_date
    if record.kind == "appointment":
        return bool(
            record.starts_at
            and record.ends_at
            and record.starts_at < window.ends_at
            and record.ends_at > window.starts_at
        )
    instant = record.deadline_at if record.kind == "deadline" else record.starts_at
    return bool(instant and window.starts_at <= instant < window.ends_at)


def _is_overdue(record: AgendaRecord, window: AgendaWindow, now: datetime) -> bool:
    if record.kind == "untimed":
        today = now.astimezone(ZoneInfo(window.display_zone)).date()
        return record.local_date < today
    due = (
        record.ends_at
        if record.kind == "appointment"
        else record.deadline_at
        if record.kind == "deadline"
        else record.starts_at
    )
    return bool(due and due < now.astimezone(UTC))


def _display_date(record: AgendaRecord, display_zone: str) -> date:
    if record.kind == "untimed":
        return record.local_date
    instant = record.deadline_at if record.kind == "deadline" else record.starts_at
    return instant.astimezone(ZoneInfo(display_zone)).date() if instant else record.local_date


def _section(record: AgendaRecord, today: date, now: datetime) -> AgendaSection:
    if record.state == "completed":
        return AgendaSection.COMPLETED
    if record.kind == "untimed" and record.local_date < today:
        return AgendaSection.OVERDUE
    due = (
        record.ends_at
        if record.kind == "appointment"
        else record.deadline_at
        if record.kind == "deadline"
        else record.starts_at
    )
    if due is not None and due < now.astimezone(UTC):
        return AgendaSection.OVERDUE
    if record.kind == "untimed":
        return AgendaSection.UNTIMED
    return AgendaSection.SCHEDULED


def _order_key(record: AgendaRecord, window: AgendaWindow, now: datetime) -> tuple[object, ...]:
    section = _section(record, now.astimezone(ZoneInfo(window.display_zone)).date(), now)
    section_rank = {
        AgendaSection.OVERDUE: 0,
        AgendaSection.SCHEDULED: 1,
        AgendaSection.UNTIMED: 2,
        AgendaSection.COMPLETED: 3,
    }[section]
    instant = record.deadline_at if record.kind == "deadline" else record.starts_at
    order_date = _display_date(record, window.display_zone)
    return (
        section_rank,
        order_date,
        instant.astimezone(UTC) if instant else datetime.min.replace(tzinfo=UTC),
        record.title.casefold(),
        str(record.occurrence_id),
    )
