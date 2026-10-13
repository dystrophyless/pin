"""Localized Telegram rendering for owner-confirmed plan changes."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pin.contexts.planning.domain.mutations import MutationAction, MutationPreview
from pin.contexts.planning.domain.plans import ResolvedSchedule


def render_mutation_preview(preview: MutationPreview, language: str) -> tuple[str, str, str, str]:
    action = preview.request.action
    if language == "ru":
        buttons = {
            MutationAction.COMPLETE: ("Отметить выполненной?", "Подтвердить", "Отмена"),
            MutationAction.CANCEL: ("Отменить задачу?", "Подтвердить", "Отмена"),
            MutationAction.RESCHEDULE: ("Перенести задачу?", "Перенести", "Отмена"),
        }[action]
        if action is MutationAction.CANCEL and preview.cancellation_projection is not None:
            buttons = ("Отменить эту и будущие задачи?", "Подтвердить", "Отмена")
        scope_label = (
            "Эта и все будущие задачи"
            if preview.request.scope.value == "this_and_future"
            else "Только эта задача"
        )
        reminder_label = "Напоминания"
    else:
        buttons = {
            MutationAction.COMPLETE: ("Mark this occurrence done?", "Confirm", "Cancel"),
            MutationAction.CANCEL: ("Cancel this occurrence?", "Confirm", "Cancel"),
            MutationAction.RESCHEDULE: ("Reschedule this occurrence?", "Reschedule", "Cancel"),
        }[action]
        if action is MutationAction.CANCEL and preview.cancellation_projection is not None:
            buttons = ("Cancel this and future occurrences?", "Confirm", "Cancel")
        scope_label = (
            "This and all future occurrences"
            if preview.request.scope.value == "this_and_future"
            else "This occurrence only"
        )
        reminder_label = "Reminders"
    before_label = _schedule_range(preview.target.schedule)
    before = f"{before_label} ({preview.target.schedule.event_zone})"
    if preview.replacement_schedule is None:
        detail = f"{preview.target.title}\n{before}"
    else:
        after = _schedule_range(preview.replacement_schedule)
        after = f"{after} ({preview.replacement_schedule.event_zone})"
        detail = f"{preview.target.title}\n{before} → {after}"
        if not preview.series_preview_schedules:
            detail += f"\n{scope_label}"
    if preview.series_preview_schedules:
        date_labels = [
            _schedule_label(item.local_date, item.local_time)
            for item in preview.series_preview_schedules
        ]
        visible = date_labels[:6]
        suffix = (
            f", … +{len(date_labels) - len(visible)}" if len(date_labels) > len(visible) else ""
        )
        detail += f"\n{scope_label}: {', '.join(visible)}{suffix}"
    if preview.cancellation_projection is not None:
        dates = [item.local_date.isoformat() for item in preview.cancellation_projection.targets]
        visible = dates[:6]
        suffix = f", … +{len(dates) - len(visible)}" if len(dates) > len(visible) else ""
        detail += f"\n{scope_label} ({len(dates)}): {', '.join(visible)}{suffix}"
    if preview.reminder_projection is not None:
        assert preview.replacement_schedule is not None
        zone = ZoneInfo(preview.replacement_schedule.event_zone)
        reminder_lines = [
            f"{item.offset.amount} {item.offset.kind.value} → "
            f"{item.due_at.astimezone(zone).strftime('%Y-%m-%d %H:%M')}"
            for item in preview.reminder_projection.scheduled
        ]
        if language == "ru":
            summary = ", ".join(reminder_lines) if reminder_lines else "нет"
            if preview.reminder_projection.omitted:
                summary += f"; пропущено: {len(preview.reminder_projection.omitted)}"
            if preview.reminder_projection.notices:
                summary += f"; перенесено: {len(preview.reminder_projection.notices)}"
        else:
            summary = ", ".join(reminder_lines) if reminder_lines else "none"
            if preview.reminder_projection.omitted:
                summary += f"; omitted: {len(preview.reminder_projection.omitted)}"
            if preview.reminder_projection.notices:
                summary += f"; shifted: {len(preview.reminder_projection.notices)}"
        detail += f"\n{reminder_label}: {summary}"
    return buttons[0], detail, buttons[1], buttons[2]


def mutation_keyboard(
    confirm_token: str,
    cancel_token: str,
    confirm_label: str,
    cancel_label: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=confirm_label, callback_data=f"pm:{confirm_token}"),
                InlineKeyboardButton(text=cancel_label, callback_data=f"pm:{cancel_token}"),
            ]
        ]
    )


def _schedule_label(day: date, local_time: time | None) -> str:
    if local_time is None:
        return day.isoformat()
    return f"{day.isoformat()} {local_time.strftime('%H:%M')}"


def _schedule_range(schedule: ResolvedSchedule) -> str:
    start = _schedule_label(schedule.local_date, schedule.local_time)
    if schedule.ends_at is None:
        return start
    end = schedule.ends_at.astimezone(ZoneInfo(schedule.event_zone))
    return f"{start} – {end.strftime('%Y-%m-%d %H:%M')}"
