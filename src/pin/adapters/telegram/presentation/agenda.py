"""RU/EN MarkdownV2 rendering for paginated saved agendas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pin.adapters.telegram.presentation.callbacks import CALLBACK_DATA_MAX_BYTES
from pin.adapters.telegram.presentation.formatting import (
    escape_markdown_v2,
    telegram_utf16_length,
)
from pin.contexts.planning.api import AgendaEntry, AgendaPage, AgendaSection

_PAGE_LIMIT = 3900


@dataclass(frozen=True, slots=True)
class AgendaButton:
    text: str
    action: str
    value: str


@dataclass(frozen=True, slots=True)
class AgendaReply:
    markdown: str
    plain_text: str
    buttons: tuple[AgendaButton, ...] = ()
    handling: str = "handled"


def render_agenda(page: AgendaPage, *, language: str) -> AgendaReply:
    if language not in {"ru", "en"}:
        raise ValueError("language must be 'ru' or 'en'")
    view = page.snapshot.window.view.value
    heading = _heading(
        view, page.snapshot.window.start_date, page.snapshot.window.display_zone, language
    )
    section_names = {
        "en": {
            AgendaSection.OVERDUE: "Overdue",
            AgendaSection.SCHEDULED: "Scheduled",
            AgendaSection.UNTIMED: "Untimed",
            AgendaSection.COMPLETED: "Completed",
        },
        "ru": {
            AgendaSection.OVERDUE: "Просрочено",
            AgendaSection.SCHEDULED: "По расписанию",
            AgendaSection.UNTIMED: "Без времени",
            AgendaSection.COMPLETED: "Выполнено",
        },
    }[language]
    plain_lines = [heading]
    markdown_lines = [f"*{escape_markdown_v2(heading)}*"]
    used = telegram_utf16_length(heading) + 2
    end_index = page.start_index
    last_section: AgendaSection | None = None
    for entry in page.entries:
        label = _entry_text(entry, page.snapshot.window.display_zone)
        prefix = (
            f"\n*{escape_markdown_v2(section_names[entry.section])}*\n"
            if entry.section != last_section
            else ""
        )
        row_plain = f"• {label}"
        row_markdown = f"• {escape_markdown_v2(label)}"
        needed = telegram_utf16_length(prefix + row_markdown + "\n")
        if plain_lines[1:] and used + needed > _PAGE_LIMIT:
            break
        plain_lines.extend(
            ([section_names[entry.section]] if entry.section != last_section else []) + [row_plain]
        )
        markdown_lines.extend(
            (
                [f"*{escape_markdown_v2(section_names[entry.section])}*"]
                if entry.section != last_section
                else []
            )
            + [row_markdown]
        )
        used += needed
        end_index = entry.snapshot_index + 1
        last_section = entry.section

    if not page.entries:
        empty = (
            "No saved items in this view. Use /today or /week after adding a plan."
            if language == "en"
            else (
                "В этом периоде нет сохранённых планов. "
                "Отправьте /today или /week после добавления плана."
            )
        )
        plain_lines.append(empty)
        markdown_lines.append(escape_markdown_v2(empty))
    else:
        # A row is bounded by the plan title limit and always fits on an empty page.
        if end_index == page.start_index:
            raise ValueError("agenda entry exceeds Telegram's page limit")

    buttons: list[AgendaButton] = []
    snapshot_id = str(page.snapshot.snapshot_id)
    if page.previous_index is not None:
        value = f"{snapshot_id}:{page.previous_index}"
        _validate_callback(value)
        buttons.append(
            AgendaButton("‹ Previous" if language == "en" else "‹ Назад", "agenda_page", value)
        )
    if any(entry.snapshot_index >= end_index for entry in page.entries):
        value = f"{snapshot_id}:{end_index}"
        _validate_callback(value)
        buttons.append(
            AgendaButton("Next ›" if language == "en" else "Далее ›", "agenda_page", value)
        )
    if page.snapshot.include_completed:
        value = f"{view}|0"
        buttons.append(
            AgendaButton(
                "Hide completed" if language == "en" else "Скрыть выполненные",
                "agenda_completed",
                value,
            )
        )
    else:
        value = f"{view}|1"
        buttons.append(
            AgendaButton(
                "Show completed" if language == "en" else "Показать выполненные",
                "agenda_completed",
                value,
            )
        )
    return AgendaReply("\n".join(markdown_lines), "\n".join(plain_lines), tuple(buttons))


def render_agenda_expired(*, language: str) -> AgendaReply:
    text = (
        "This agenda page expired. Send /today or /week to open a fresh view."
        if language == "en"
        else (
            "Срок этой страницы истёк. Отправьте /today или /week, чтобы открыть актуальный список."
        )
    )
    return AgendaReply(escape_markdown_v2(text), text)


def render_agenda_unavailable(*, language: str) -> AgendaReply:
    text = (
        "Saved plans are unavailable for this account."
        if language == "en"
        else "Сохранённые планы недоступны для этого аккаунта."
    )
    return AgendaReply(escape_markdown_v2(text), text)


def _heading(view: str, start_date: date, zone: str, language: str) -> str:
    label = (
        ("Today" if view == "today" else "This week")
        if language == "en"
        else ("Сегодня" if view == "today" else "Эта неделя")
    )
    return f"{label} · {start_date.isoformat()} · {zone}"


def _entry_text(entry: AgendaEntry, display_zone: str) -> str:
    record = entry.record
    title = record.title
    if record.kind == "untimed":
        date_text = record.local_date.isoformat()
        zone_text = f" ({record.event_zone})" if record.event_zone != display_zone else ""
        return f"{date_text} · {title}{zone_text}"

    if record.kind == "deadline":
        local_when = f"{record.local_date.isoformat()} {record.local_time:%H:%M}"
    else:
        assert record.local_time is not None
        local_when = f"{record.local_date.isoformat()} {record.local_time:%H:%M}"
        if record.kind == "appointment" and record.end_local_time is not None:
            end_date = record.end_local_date or record.local_date
            local_when += f"–{end_date.isoformat()} {record.end_local_time:%H:%M}"
    zone_text = f" ({record.event_zone})" if record.event_zone != display_zone else ""
    return f"{entry.display_date.isoformat()} · {local_when}{zone_text} · {title}"


def _validate_callback(value: str) -> None:
    if len(value.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
        raise ValueError("agenda callback exceeds Telegram's size limit")
