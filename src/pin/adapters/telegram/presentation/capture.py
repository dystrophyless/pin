"""Localized safe rendering for durable text-capture replies and previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
    kind_label = str(details.get("kind", kind or "plan"))
    lines = [f"{title} — {kind_label}"]
    if kind == "plan":
        event = details.get("starts_at") or details.get("deadline_at")
        local_date = details.get("local_date")
        local_time = details.get("local_time")
        zone = details.get("event_zone")
        if event:
            lines.append(f"{_label(language, 'Time', 'Время')}: {event}")
        elif local_date:
            lines.append(f"{_label(language, 'Date', 'Дата')}: {local_date}")
        if local_time and not event:
            lines.append(
                f"{_label(language, 'Local time', 'Местное время')}: {local_time} ({zone})"
            )
        if zone:
            lines.append(f"{_label(language, 'Event zone', 'Часовой пояс события')}: {zone}")
        duration = details.get("duration_seconds")
        if duration:
            lines.append(
                f"{_label(language, 'Duration', 'Длительность')}: {int(duration) // 60} min"
            )
        lines.extend(_reminder_lines(details, language))
    elif kind == "recurring":
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
                lines.append(f"• {event or date_text}")
                gap = occurrence.get("dst_gap_shift_seconds")
                repeated = occurrence.get("repeated_time_choice")
                if gap:
                    lines.append(
                        f"  {_label(language, 'DST adjustment', 'Коррекция перехода времени')}: "
                        f"+{gap}s"
                    )
                if repeated:
                    lines.append(
                        f"  {_label(language, 'Repeated time', 'Повторяющееся местное время')}: "
                        f"{repeated}"
                    )
                lines.extend(_reminder_lines(occurrence, language, indent="  "))
        if details.get("truncated"):
            lines.append(
                _label(
                    language,
                    "More occurrences will be generated later.",
                    "Остальные повторы будут сформированы позднее.",
                )
            )
    return "\n".join(lines)


def _reminder_lines(data: Mapping[str, object], language: str, *, indent: str = "") -> list[str]:
    lines: list[str] = []
    reminders = data.get("reminders")
    if isinstance(reminders, list):
        if not reminders:
            lines.append(f"{indent}{_label(language, 'Reminders: none', 'Напоминания: нет')}")
        for reminder in reminders:
            if not isinstance(reminder, Mapping):
                continue
            lines.append(
                f"{indent}{_label(language, 'Reminder', 'Напоминание')}: "
                f"{reminder.get('due_at')} ({reminder.get('source', 'explicit')}, "
                f"{reminder.get('offset_kind')} {reminder.get('amount')})"
            )
    omissions = data.get("omitted_reminders")
    if isinstance(omissions, list):
        for omission in omissions:
            if isinstance(omission, Mapping):
                lines.append(
                    f"{indent}{_label(language, 'Omitted reminder', 'Не включено')}: "
                    f"{omission.get('reason')} ({omission.get('offset_kind')} "
                    f"{omission.get('amount')})"
                )
    notices = data.get("notices")
    if isinstance(notices, list):
        for notice in notices:
            if isinstance(notice, Mapping):
                quiet_label = _label(language, "Quiet-hours adjustment", "Перенос из тихих часов")
                lines.append(
                    f"{indent}{quiet_label}: "
                    f"{notice.get('original_due_at')} → {notice.get('due_at')}"
                )
    return lines


def _label(language: str, english: str, russian: str) -> str:
    return english if language == "en" else russian


def _escape(value: str) -> str:
    from pin.adapters.telegram.presentation.formatting import escape_markdown_v2

    return escape_markdown_v2(value)
