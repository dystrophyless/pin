from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from pin.adapters.telegram.agenda import agenda_command
from pin.adapters.telegram.presentation.agenda import render_agenda
from pin.adapters.telegram.presentation.callbacks import (
    CallbackBinding,
    CallbackTokens,
    InvalidCallbackToken,
)
from pin.contexts.planning.application.agenda import (
    AgendaRecord,
    AgendaSection,
    AgendaSnapshot,
    AgendaView,
    agenda_window,
    open_agenda,
    read_agenda_page,
    remember_page_start,
)


def _record(
    owner_id: UUID,
    *,
    kind: str = "task",
    state: str = "open",
    title: str = "Plan",
    zone: str = "UTC",
    local_day: date = date(2026, 6, 2),
    local_time: time | None = time(9),
    starts_at: datetime | None = datetime(2026, 6, 2, 9, tzinfo=UTC),
    ends_at: datetime | None = None,
    deadline_at: datetime | None = None,
) -> AgendaRecord:
    if kind == "untimed":
        local_time = starts_at = ends_at = deadline_at = None
    if kind == "appointment" and ends_at is None:
        ends_at = starts_at + timedelta(hours=1) if starts_at else None
    if kind == "deadline":
        deadline_at = deadline_at or starts_at
        starts_at = None
    return AgendaRecord(
        uuid4(),
        owner_id,
        kind,
        title,
        state,
        zone,
        local_day,
        local_time,
        None,
        None,
        starts_at,
        ends_at,
        deadline_at,
    )


class _MemoryAgendaStore:
    def __init__(self, records: tuple[AgendaRecord, ...]) -> None:
        self.records = list(records)
        self.snapshots: dict[UUID, AgendaSnapshot] = {}

    async def list_candidates(self, owner_id, *, window, include_completed):
        return tuple(record for record in self.records if record.owner_id == owner_id)

    async def save_snapshot(self, snapshot):
        self.snapshots[snapshot.snapshot_id] = snapshot

    async def get_snapshot(self, owner_id, snapshot_id, *, now):
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None or snapshot.owner_id != owner_id or snapshot.expires_at <= now:
            return None
        return snapshot

    async def list_snapshot_records(self, owner_id, occurrence_ids):
        return tuple(
            record
            for record in self.records
            if record.owner_id == owner_id and record.occurrence_id in occurrence_ids
        )

    async def remember_page_start(self, owner_id, snapshot_id, *, start_index):
        snapshot = self.snapshots[snapshot_id]
        if snapshot.owner_id == owner_id:
            starts = tuple(sorted(set((*snapshot.page_starts, start_index))))
            self.snapshots[snapshot_id] = replace(snapshot, page_starts=starts)


class _MemoryCallbackStore:
    def __init__(self) -> None:
        self.bindings: dict[str, CallbackBinding] = {}

    async def issue(self, binding: CallbackBinding) -> None:
        self.bindings[binding.token_hash] = binding

    async def consume(self, token_hash: str, owner_id: UUID, now: datetime):
        binding = self.bindings.pop(token_hash, None)
        if (
            binding is None
            or binding.owner_id != owner_id
            or binding.expires_at <= now
        ):
            return None
        return binding


@pytest.mark.parametrize(
    ("local_now", "hours"),
    [
        (datetime(2026, 3, 29, 12, tzinfo=ZoneInfo("Europe/Berlin")), 23),
        (datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Berlin")), 25),
    ],
)
def test_today_window_uses_local_midnights_across_dst(local_now: datetime, hours: int) -> None:
    window = agenda_window(AgendaView.TODAY, "Europe/Berlin", local_now)
    assert window.ends_at - window.starts_at == timedelta(hours=hours)


@pytest.mark.parametrize(
    ("local_now", "hours"),
    [
        (datetime(2026, 3, 29, 12, tzinfo=ZoneInfo("Europe/Berlin")), 167),
        (datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Berlin")), 169),
    ],
)
def test_week_window_starts_monday_and_maps_dst(local_now: datetime, hours: int) -> None:
    window = agenda_window(AgendaView.WEEK, "Europe/Berlin", local_now)
    assert window.start_date.weekday() == 0
    assert window.end_date - window.start_date == timedelta(days=7)
    assert window.ends_at - window.starts_at == timedelta(hours=hours)


@pytest.mark.asyncio
async def test_agenda_uses_half_open_bounds_and_preserves_old_untimed_items() -> None:
    owner = uuid4()
    now = datetime(2026, 6, 2, 12, tzinfo=UTC)
    rows = (
        _record(owner, title="At start", starts_at=datetime(2026, 6, 2, 0, tzinfo=UTC)),
        _record(owner, title="At end", starts_at=datetime(2026, 6, 3, 0, tzinfo=UTC)),
        _record(
            owner,
            title="Overdue",
            local_day=date(2026, 6, 1),
            starts_at=datetime(2026, 6, 1, 23, tzinfo=UTC),
        ),
        _record(owner, title="Cancelled", state="cancelled"),
        _record(owner, title="Completed", state="completed"),
        _record(owner, kind="untimed", title="Older untimed", local_day=date(2026, 5, 1)),
        _record(owner, kind="untimed", title="Today untimed"),
    )
    store = _MemoryAgendaStore(rows)
    page = await open_agenda(
        store, owner_id=owner, view=AgendaView.TODAY, display_zone="UTC", now=now
    )
    by_title = {entry.record.title: entry for entry in page.entries}
    assert set(by_title) == {"At start", "Overdue", "Older untimed", "Today untimed"}
    assert by_title["Older untimed"].section is AgendaSection.OVERDUE
    assert by_title["Today untimed"].section is AgendaSection.UNTIMED
    older = render_agenda(page, language="en")
    assert "Older untimed" in older.plain_text
    assert "Older untimed" in older.plain_text and "00:00" not in older.plain_text

    completed = await open_agenda(
        store,
        owner_id=owner,
        view=AgendaView.TODAY,
        display_zone="UTC",
        include_completed=True,
        now=now,
    )
    assert any(entry.record.title == "Completed" for entry in completed.entries)
    assert all(entry.record.title != "Cancelled" for entry in completed.entries)


@pytest.mark.asyncio
async def test_home_date_can_differ_from_event_zone_date() -> None:
    owner = uuid4()
    event = _record(
        owner,
        title="Night shift",
        zone="America/New_York",
        local_day=date(2026, 6, 1),
        local_time=time(20, 30),
        starts_at=datetime(2026, 6, 2, 0, 30, tzinfo=UTC),
    )
    page = await open_agenda(
        _MemoryAgendaStore((event,)),
        owner_id=owner,
        view=AgendaView.TODAY,
        display_zone="Asia/Almaty",
        now=datetime(2026, 6, 2, 0, tzinfo=UTC),
    )
    entry = page.entries[0]
    assert entry.display_date == date(2026, 6, 2)
    reply = render_agenda(page, language="en")
    assert "America/New\\_York" in reply.markdown
    assert "2026-06-01 20:30" in reply.plain_text


@pytest.mark.asyncio
async def test_snapshot_keeps_membership_order_across_updates_and_persisted_pages() -> None:
    owner = uuid4()
    now = datetime(2026, 6, 2, 8, tzinfo=UTC)
    rows = tuple(
        _record(
            owner,
            title=f"Plan {index} " + "_" * 450,
            starts_at=datetime(2026, 6, 2, 9 + index, tzinfo=UTC),
            local_time=time(9 + index),
        )
        for index in range(6)
    )
    store = _MemoryAgendaStore(rows)
    first = await open_agenda(
        store, owner_id=owner, view=AgendaView.TODAY, display_zone="UTC", now=now
    )
    first_reply = render_agenda(first, language="en")
    assert len(first_reply.markdown) <= 3900
    next_button = next(button for button in first_reply.buttons if button.action == "agenda_page")
    snapshot_text, offset_text = next_button.value.split(":")
    offset = int(offset_text)
    assert UUID(snapshot_text) == first.snapshot.snapshot_id
    await remember_page_start(
        store, owner_id=owner, snapshot_id=first.snapshot.snapshot_id, start_index=offset
    )

    store.records[-1] = replace(store.records[-1], title="Updated after page one")
    second = await read_agenda_page(
        store,
        owner_id=owner,
        snapshot_id=first.snapshot.snapshot_id,
        start_index=offset,
        now=now + timedelta(minutes=1),
    )
    assert second is not None
    assert second.previous_index == 0
    frozen_ids = first.snapshot.item_ids
    assert tuple(str(entry.occurrence_id) for entry in second.entries) == frozen_ids[offset:]
    expected_titles = [row.title for row in rows[offset:-1]] + ["Updated after page one"]
    assert [entry.record.title for entry in second.entries] == expected_titles
    second_reply = render_agenda(second, language="ru")
    assert len(second_reply.markdown) <= 3900
    assert any(button.action == "agenda_page" for button in second_reply.buttons)


@pytest.mark.asyncio
async def test_empty_agenda_is_usable_in_both_languages() -> None:
    owner = uuid4()
    page = await open_agenda(
        _MemoryAgendaStore(()),
        owner_id=owner,
        view=AgendaView.TODAY,
        display_zone="UTC",
        now=datetime(2026, 6, 2, 8, tzinfo=UTC),
    )
    english = render_agenda(page, language="en")
    russian = render_agenda(page, language="ru")
    assert "No saved items" in english.plain_text
    assert "нет сохранённых" in russian.plain_text
    assert any(button.action == "agenda_completed" for button in english.buttons)
    assert any(button.action == "agenda_completed" for button in russian.buttons)


def test_agenda_commands_are_exact_and_accept_bot_mentions() -> None:
    assert agenda_command("/today") is AgendaView.TODAY
    assert agenda_command("/week@pin_bot") is AgendaView.WEEK
    assert agenda_command("/today please") is AgendaView.TODAY
    assert agenda_command("today") is None


@pytest.mark.asyncio
async def test_callback_action_version_map_rejects_unknown_actions() -> None:
    owner, now = uuid4(), datetime(2026, 6, 2, 8, tzinfo=UTC)
    callbacks = CallbackTokens(_MemoryCallbackStore())
    token = await callbacks.issue(
        owner_id=owner,
        action="unknown-action",
        target_id="opaque",
        target_version=1,
        expires_at=now + timedelta(minutes=5),
        now=now,
    )
    with pytest.raises(InvalidCallbackToken):
        await callbacks.consume(
            token,
            actor_id=owner,
            current_target_versions={"agenda_page": 1},
            now=now,
        )
