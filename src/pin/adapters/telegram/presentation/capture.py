"""Localized safe rendering for durable text-capture replies and previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pin.adapters.telegram.presentation.formatting import (
    MessageEntry,
    MessagePage,
    paginate_entries,
)

__all__ = [
    "build_capture_keyboard",
    "capture_choice_callback_data",
    "capture_confirmation_callback_data",
    "render_capture_response",
]

CAPTURE_CANCEL_ACTION = "capture.cancel"
CAPTURE_CONFIRM_PREFIX = "capc:"
CAPTURE_CANCEL_PREFIX = "capx:"
CAPTURE_CHOICE_PREFIX = "capq:"


def capture_confirmation_callback_data(credential: str) -> str:
    """Embed the proof's only plaintext credential in a bounded Telegram callback."""
    if not credential or not credential.isascii():
        raise ValueError("confirmation credential must be non-empty ASCII")
    callback_data = CAPTURE_CONFIRM_PREFIX + credential
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Telegram confirmation callback data exceeds 64 bytes")
    return callback_data


def capture_choice_callback_data(credential: str) -> str:
    """Embed an owner/version-bound choice token in a bounded Telegram callback."""
    if not credential or not credential.isascii():
        raise ValueError("choice credential must be non-empty ASCII")
    callback_data = CAPTURE_CHOICE_PREFIX + credential
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Telegram clarification callback data exceeds 64 bytes")
    return callback_data


def render_capture_response(
    response: Mapping[str, object], *, language: str
) -> tuple[MessagePage, ...]:
    """Build escaped MarkdownV2 pages from the persisted capture response payload."""
    if language not in {"ru", "en"}:
        raise ValueError("capture presentation language must be ru or en")
    kind = response.get("kind")
    if kind == "preview":
        preview = response.get("preview")
        if not isinstance(preview, Mapping):
            raise ValueError("preview response has no preview projection")
        title = "Plan preview" if language == "en" else "Предварительный просмотр"
        items = preview.get("items")
        if not isinstance(items, list):
            items = []
        entries = [
            MessageEntry(_item_text(item, language)) for item in items if isinstance(item, Mapping)
        ]
        pages = paginate_entries(
            title,
            entries,
            empty_text=("Nothing to review" if language == "en" else "Нет данных для проверки"),
        )
        explanation = response.get("text")
        if isinstance(explanation, str) and explanation.strip():
            tail = "\n" + explanation
            last = pages[-1]
            pages[-1] = MessagePage(
                markdown=last.markdown + "\n" + _escape(explanation),
                plain_text=last.plain_text + tail,
                target_ids=last.target_ids,
            )
        return tuple(pages)

    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        if kind == "confirmed":
            text = "Saved." if language == "en" else "Сохранено."
        elif kind == "cancelled":
            text = (
                "Draft cancelled; nothing was saved."
                if language == "en"
                else "Черновик отменён; ничего не сохранено."
            )
        else:
            text = (
                "I could not prepare that request."
                if language == "en"
                else "Не удалось подготовить запрос."
            )
    heading = "Plan request" if language == "en" else "Запрос на план"
    page = paginate_entries(heading, (MessageEntry(text),), empty_text=text)[0]
    return (page,)


def build_capture_keyboard(
    *,
    confirm_callback_data: str | None = None,
    cancel_callback_data: str | None = None,
    choices: Sequence[Mapping[str, object]] = (),
    choice_callback_data: Mapping[str, str] | None = None,
    language: str,
) -> InlineKeyboardMarkup | None:
    """Build one final-page action keyboard; caller provides owner-bound callback data."""
    if language not in {"ru", "en"}:
        raise ValueError("capture presentation language must be ru or en")
    rows: list[list[InlineKeyboardButton]] = []
    callbacks = choice_callback_data or {}
    for choice in choices:
        choice_id = choice.get("id")
        label = choice.get("label")
        if not isinstance(choice_id, str) or not isinstance(label, str):
            continue
        callback_data = callbacks.get(choice_id)
        if callback_data is None:
            continue
        if len(callback_data.encode("utf-8")) > 64:
            raise ValueError("Telegram clarification callback data exceeds 64 bytes")
        rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    if confirm_callback_data is not None:
        if len(confirm_callback_data.encode("utf-8")) > 64:
            raise ValueError("Telegram confirmation callback data exceeds 64 bytes")
        rows.append(
            [
                InlineKeyboardButton(
                    text="Confirm and save" if language == "en" else "Подтвердить и сохранить",
                    callback_data=confirm_callback_data,
                )
            ]
        )
    if cancel_callback_data is not None:
        if len(cancel_callback_data.encode("utf-8")) > 64:
            raise ValueError("Telegram cancellation callback data exceeds 64 bytes")
        rows.append(
            [
                InlineKeyboardButton(
                    text="Cancel draft" if language == "en" else "Отменить черновик",
                    callback_data=cancel_callback_data,
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _item_text(item: Mapping[str, object], language: str) -> str:
    kind = item.get("kind")
    details = item.get("details")
    if not isinstance(details, Mapping):
        return "Invalid preview item" if language == "en" else "Некорректный пункт"
    title = str(
        details.get("title", "Recurring plan" if language == "en" else "Повторяющийся план")
    )
    kind_label = _plan_kind(details.get("kind", kind or "plan"), language)
    lines = [f"{title} — {kind_label}"]
    if kind == "plan":
        event = details.get("starts_at") or details.get("deadline_at")
        local_date = details.get("local_date")
        local_time = details.get("local_time")
        zone = details.get("event_zone")
        if isinstance(event, str) and event:
            lines.append(f"{_label(language, 'Time', 'Время')}: {_instant_text(event, zone)}")
        elif local_date:
            local_schedule = str(local_date)
            if isinstance(local_time, str) and local_time:
                local_schedule += f" {_clock(local_time)}"
            lines.append(
                f"{_label(language, 'Date', 'Дата')}: {local_schedule}"
            )
        if zone:
            lines.append(f"{_label(language, 'Event zone', 'Часовой пояс события')}: {zone}")
        duration = details.get("duration_seconds")
        if duration:
            lines.append(
                f"{_label(language, 'Duration', 'Длительность')}: "
                f"{int(duration) // 60} {_label(language, 'min', 'мин')}"
            )
        lines.extend(_reminder_lines(details, language, event_zone=zone))
    elif kind == "recurring":
        zone = details.get("event_zone")
        through = details.get("through")
        if through:
            lines.append(f"{_label(language, 'Preview through', 'Предпросмотр до')}: {through}")
        occurrences = details.get("occurrences")
        if isinstance(occurrences, list):
            for occurrence in occurrences:
                if not isinstance(occurrence, Mapping):
                    continue
                event = occurrence.get("starts_at") or occurrence.get("deadline_at")
                date_text = occurrence.get("local_date")
                sequence = occurrence.get("sequence")
                prefix = (
                    f"{_label(language, 'Occurrence', 'Повтор')} {sequence}: "
                    if isinstance(sequence, int)
                    else "• "
                )
                schedule = (
                    _instant_text(event, zone)
                    if isinstance(event, str) and event
                    else str(date_text or _label(language, 'Date unavailable', 'Дата недоступна'))
                )
                lines.append(f"{prefix}{schedule}")
                gap = occurrence.get("dst_gap_shift_seconds")
                repeated = occurrence.get("repeated_time_choice")
                if gap:
                    lines.append(
                        f"  {_label(language, 'DST gap shift', 'Сдвиг из-за перехода времени')}: "
                        f"+{gap} {_label(language, 'seconds', 'сек.')}"
                    )
                if repeated:
                    repeated_text = {
                        "earlier": _label(language, "earlier occurrence", "более ранний вариант"),
                    }.get(str(repeated), str(repeated).replace("_", " "))
                    lines.append(
                        f"  {_label(language, 'Repeated local time', 'Повтор местного времени')}: "
                        f"{repeated_text}"
                    )
                lines.extend(_reminder_lines(occurrence, language, indent="  ", event_zone=zone))
        if details.get("truncated"):
            lines.append(
                _label(
                    language,
                    "More occurrences will be generated later.",
                    "Остальные повторы будут сформированы позднее.",
                )
            )
    return "\n".join(lines)


def _reminder_lines(
    data: Mapping[str, object],
    language: str,
    *,
    indent: str = "",
    event_zone: object = None,
) -> list[str]:
    lines: list[str] = []
    reminders = data.get("reminders")
    if isinstance(reminders, list):
        if not reminders:
            lines.append(f"{indent}{_label(language, 'Reminders: none', 'Напоминания: нет')}")
        for reminder in reminders:
            if not isinstance(reminder, Mapping):
                continue
            offset_kind = reminder.get("offset_kind", reminder.get("kind"))
            amount = reminder.get("amount", reminder.get("offset_amount"))
            source = reminder.get("source")
            offset = _offset_text(offset_kind, amount, language)
            source_text = _source_text(source, language)
            due_at = reminder.get("due_at")
            due_text = _instant_text(due_at, event_zone)
            lines.append(
                f"{indent}{_label(language, 'Reminder', 'Напоминание')}: {offset}; "
                f"{source_text}; {_label(language, 'due', 'время')}: {due_text}"
            )
    omissions = data.get("omitted_reminders")
    if isinstance(omissions, list):
        for omission in omissions:
            if isinstance(omission, Mapping):
                offset = _offset_text(
                    omission.get("offset_kind", omission.get("kind")),
                    omission.get("amount", omission.get("offset_amount")),
                    language,
                )
                reason = _omission_reason(omission.get("reason"), language)
                lines.append(
                    f"{indent}{_label(language, 'Reminder omitted', 'Напоминание пропущено')}: "
                    f"{offset}; {reason}"
                )
    notices = data.get("notices")
    if isinstance(notices, list):
        for notice in notices:
            if isinstance(notice, Mapping):
                kind = notice.get("kind")
                original = _instant_text(notice.get("original_due_at"), event_zone)
                due = _instant_text(notice.get("due_at"), event_zone)
                if kind == "quiet_conflict":
                    text = _label(
                        language,
                        f"Quiet-hours conflict; kept at {original}",
                        f"Конфликт с тихими часами; оставлено на {original}",
                    )
                elif kind == "quiet_deferred":
                    text = _label(
                        language,
                        f"Moved out of quiet hours: {original} → {due}",
                        f"Перенесено на время после тихих часов: {original} → {due}",
                    )
                else:
                    text = _label(
                        language,
                        f"Quiet-hours notice: {original} → {due}",
                        f"Уведомление о тихих часах: {original} → {due}",
                    )
                lines.append(f"{indent}{text}")
    return lines


def _plan_kind(value: object, language: str) -> str:
    return {
        "task": _label(language, "Task", "Задача"),
        "appointment": _label(language, "Appointment", "Встреча"),
        "deadline": _label(language, "Deadline", "Срок"),
        "untimed": _label(language, "Untimed task", "Задача без времени"),
    }.get(str(value), str(value).replace("_", " "))


def _offset_text(kind: object, amount: object, language: str) -> str:
    if type(amount) is not int or amount <= 0:
        return _label(language, "reminder offset unavailable", "интервал напоминания недоступен")
    if kind == "calendar_days":
        return _plural_offset(
            amount,
            ("calendar day", "calendar days"),
            ("календарный день", "календарных дня", "календарных дней"),
            language,
        )
    if kind == "elapsed_seconds":
        units = (
            (86_400, "day", "день"),
            (3_600, "hour", "час"),
            (60, "minute", "минута"),
            (1, "second", "секунда"),
        )
        divisor, english_unit, russian_unit = next(
            ((size, en, ru) for size, en, ru in units if amount % size == 0), units[-1]
        )
        count = amount // divisor
        if language == "en":
            unit = english_unit if count == 1 else f"{english_unit}s"
            return f"{count} {unit} before (elapsed)"
        forms = {
            "день": ("день", "дня", "дней"),
            "час": ("час", "часа", "часов"),
            "минута": ("минуту", "минуты", "минут"),
            "секунда": ("секунду", "секунды", "секунд"),
        }[russian_unit]
        return f"за {count} {_russian_plural_form(count, forms)} до события (по истекшему времени)"
    return _label(language, "reminder offset unavailable", "интервал напоминания недоступен")


def _plural_offset(
    amount: int,
    english_forms: tuple[str, str],
    russian_forms: tuple[str, str, str],
    language: str,
) -> str:
    if language == "en":
        unit = english_forms[0] if amount == 1 else english_forms[1]
        return f"{amount} {unit} before"
    return f"за {amount} {_russian_plural_form(amount, russian_forms)} до события"


def _russian_plural_form(count: int, forms: tuple[str, str, str]) -> str:
    remainder_10 = count % 10
    remainder_100 = count % 100
    index = 0 if remainder_10 == 1 and remainder_100 != 11 else (
        1 if 2 <= remainder_10 <= 4 and not 12 <= remainder_100 <= 14 else 2
    )
    return forms[index]


def _source_text(value: object, language: str) -> str:
    if value is None:
        value = "explicit"
    return {
        "default": _label(language, "default", "по умолчанию"),
        "explicit": _label(language, "explicitly requested", "задано явно"),
    }.get(str(value), _label(language, "source unavailable", "источник недоступен"))


def _omission_reason(value: object, language: str) -> str:
    return {
        "past": _label(language, "already in the past", "время уже прошло"),
        "quiet_unusable": _label(
            language,
            "would be too late after quiet hours",
            "после тихих часов будет слишком поздно",
        ),
    }.get(
        str(value),
        _label(language, "omitted by schedule rules", "пропущено по правилам расписания"),
    )


def _instant_text(value: object, zone: object) -> str:
    if not isinstance(value, str) or not value:
        return "—"
    if not isinstance(zone, str) or not zone:
        return value
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return value
        local = instant.astimezone(ZoneInfo(zone))
    except (ValueError, ZoneInfoNotFoundError):
        return value
    utc = instant.astimezone(UTC)
    return f"{_wall_datetime(local)} ({zone}; UTC {_wall_datetime(utc)})"


def _wall_datetime(value: datetime) -> str:
    timespec = "seconds" if value.second or value.microsecond else "minutes"
    return f"{value.date().isoformat()} {value.time().isoformat(timespec=timespec)}"


def _clock(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(f"2000-01-01T{value}")
    except ValueError:
        return value
    timespec = "seconds" if parsed.second or parsed.microsecond else "minutes"
    return parsed.time().isoformat(timespec=timespec)


def _label(language: str, english: str, russian: str) -> str:
    return english if language == "en" else russian


def _escape(value: str) -> str:
    from pin.adapters.telegram.presentation.formatting import escape_markdown_v2

    return escape_markdown_v2(value)
