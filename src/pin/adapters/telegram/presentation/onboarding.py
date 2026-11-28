"""Localized bot start and preference previews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.contexts.identity.application.contracts import PreferencesSnapshot
from pin.contexts.identity.application.onboarding import OnboardingResult


@dataclass(frozen=True, slots=True)
class OnboardingButton:
    text: str
    action: str
    value: str


@dataclass(frozen=True, slots=True)
class OnboardingReply:
    markdown: str
    plain_text: str
    handling: str = "handled"
    buttons: tuple[OnboardingButton, ...] = ()


def render_start(profile: PreferencesSnapshot, *, ai_enabled: bool) -> OnboardingReply:
    if profile.language == "ru":
        intro = (
            "Отправьте задачу текстом или голосом."
            if ai_enabled
            else "Для задач текстом и голосом включите ИИ в /privacy."
        )
        zone = f"Часовой пояс для планов и напоминаний: {profile.home_zone}."
    else:
        intro = (
            "Send a task as text or voice."
            if ai_enabled
            else "Enable AI in /privacy to add tasks by text or voice."
        )
        zone = f"Time zone for plans and reminders: {profile.home_zone}."
    return _reply(
        f"{intro}\n{zone}",
        buttons=(_button(profile.language, "settings", "settings"),),
    )


def render_device_timezone(profile: PreferencesSnapshot) -> OnboardingReply:
    text = (
        f"Текущий часовой пояс — {profile.home_zone}. Вручную его можно выбрать в /settings."
        if profile.language == "ru"
        else f"Your time zone is {profile.home_zone}. To choose it manually, open /settings."
    )
    return _reply(text)


def render_onboarding(result: OnboardingResult) -> OnboardingReply | None:
    language = result.preferences.language if result.preferences else _language(result.draft)
    messages = {
        "cancelled": (
            "Preview cancelled. Saved preferences and access are unchanged.",
            "Предпросмотр отменён. Сохранённые настройки и доступ не изменились.",
        ),
        "not_in_progress": (
            "Send /settings to open preferences.",
            "Отправьте /settings, чтобы открыть настройки.",
        ),
        "version_conflict": (
            "Preferences changed in another session. Send /settings to review the current version.",
            "Настройки изменились в другой сессии. Отправьте /settings, чтобы проверить версию.",
        ),
        "draft_conflict": (
            "This edit changed in another session. Send /settings to load the latest version.",
            "Настройки изменились в другой сессии. Отправьте /settings, чтобы загрузить версию.",
        ),
        "unhandled": (
            "Plan capture is not available in this local version. Your message was not saved.",
            "Создание планов пока недоступно в этой локальной версии. Сообщение не сохранено.",
        ),
    }
    language_index = 0 if language == "en" else 1
    if result.state in messages:
        handling = "unhandled" if result.state == "unhandled" else "handled"
        return _reply(messages[result.state][language_index], handling=handling)
    if result.state == "confirmed" and result.preferences:
        return _reply(
            _profile_text(result.preferences),
            buttons=(_button(language, "settings", "settings"),),
        )

    if result.state not in {"started", "resumed", "advanced", "invalid_input", "confirm_required"}:
        return None

    draft = result.draft or {}
    home_zone = draft.get("home_zone")
    prompts = {
        "language": (
            "Choose your conversation language.",
            "Выберите язык общения.",
        ),
        "home_zone": (
            "Send a city in your time zone, e.g. London or New York. "
            f"You can also send a zone name like `Europe/London`. Current time zone: {home_zone}.",
            "Напишите город в вашем часовом поясе, например Москва или Алматы. "
            f"Можно также указать пояс, например `Europe/Moscow`. Текущий пояс: {home_zone}.",
        ),
        "reminders": (
            "Reminder suggestions: one calendar day and one elapsed hour before "
            "timed plans. Reply `suggested` or send two numbers: calendar days, "
            "elapsed hours (example: `1, 1`).",
            "Предлагаемые напоминания: за календарный день и за час до планов "
            "со временем. Отправьте `по умолчанию` или два числа: календарные "
            "дни и часы (пример: `1, 1`).",
        ),
        "quiet_review": (
            "Quiet hours and evening review suggestions: `22:00-08:00; 20:00`. "
            "Reply `suggested` or send `quiet-start quiet-end; review-time`.",
            "Предлагаемые тихие часы и время обзора: `22:00-08:00; 20:00`. "
            "Отправьте `по умолчанию` или `начало-конец; время-обзора`.",
        ),
        "confirm": (
            "Reply `confirm` to save these preferences, or send /cancel.",
            "Отправьте `подтверждаю`, чтобы сохранить настройки, или /cancel.",
        ),
    }
    step = result.step or "language"
    text = prompts[step][language_index]
    if step == "confirm" and result.state != "confirm_required":
        text = f"{_summary(draft, language)}\n{text}"
    if result.state == "invalid_input":
        retry = ("That value is not valid. Please try again.", "Это значение не подходит.")
        text = f"{retry[language_index]}\n{text}"
    return _reply(text, buttons=_step_buttons(language, step))


def _step_buttons(language: str, step: str) -> tuple[OnboardingButton, ...]:
    buttons: list[OnboardingButton] = []
    if step == "language":
        buttons.extend(
            (
                _button(language, "en", "answer", "language|en"),
                _button(language, "ru", "answer", "language|ru"),
            )
        )
    elif step in {"reminders", "quiet_review"}:
        buttons.append(_button(language, "suggested", "answer", f"{step}|suggested"))
    elif step == "confirm":
        buttons.append(_button(language, "confirm", "answer", "confirm|confirm"))
    if step != "confirm":
        buttons.append(_button(language, "keep", "answer", f"{step}|keep"))
    buttons.append(_button(language, "cancel", "cancel", "draft"))
    return tuple(buttons)


def _button(language: str, key: str, action: str, value: str | None = None) -> OnboardingButton:
    labels = {
        "en": {
            "en": "English",
            "ru": "Русский",
            "suggested": "Use suggestions",
            "keep": "Keep current",
            "confirm": "Confirm",
            "cancel": "Cancel",
            "settings": "Edit preferences",
        },
        "ru": {
            "en": "English",
            "ru": "Русский",
            "suggested": "Использовать предложенное",
            "keep": "Оставить текущие",
            "confirm": "Подтвердить",
            "cancel": "Отмена",
            "settings": "Изменить настройки",
        },
    }
    return OnboardingButton(labels[language][key], action, value if value is not None else key)


def _language(draft: dict[str, object] | None) -> str:
    return "ru" if draft and draft.get("language") == "ru" else "en"


def _summary(draft: dict[str, object], language: str) -> str:
    day = _int_value(draft.get("reminder_calendar_days"), 1)
    hour = _int_value(draft.get("reminder_elapsed_hours"), 1)
    quiet_start = _clock(str(draft.get("quiet_start", "22:00")))
    quiet_end = _clock(str(draft.get("quiet_end", "08:00")))
    review = _clock(str(draft.get("review_time", "20:00")))
    if language == "ru":
        return (
            f"**Проверьте настройки:**\nЯзык: {draft.get('language')}\n"
            f"Домашний пояс: {draft.get('home_zone')}\n"
            f"Напоминания: за {day} календарный день и {hour} ч.\n"
            f"Тихие часы: {quiet_start}–{quiet_end}\nВечерний обзор: {review}"
        )
    return (
        f"**Review your preferences:**\nLanguage: {draft.get('language')}\n"
        f"Home zone: {draft.get('home_zone')}\n"
        f"Reminders: {day} calendar day(s) and {hour} elapsed hour(s) before\n"
        f"Quiet hours: {quiet_start}–{quiet_end}\nEvening review: {review}"
    )


def _profile_text(preferences: PreferencesSnapshot) -> str:
    if preferences.language == "ru":
        return (
            f"Настройки сохранены.\nЯзык: {preferences.language}\n"
            f"Домашний пояс: {preferences.home_zone}\n"
            f"Напоминания: за {preferences.reminder_calendar_days} календарный день "
            f"и {preferences.reminder_elapsed_hours} ч.\n"
            f"Тихие часы: {_clock(preferences.quiet_start.isoformat())}–"
            f"{_clock(preferences.quiet_end.isoformat())}\n"
            f"Вечерний обзор: {_clock(preferences.review_time.isoformat())}"
        )
    return (
        f"Preferences saved.\nLanguage: {preferences.language}\n"
        f"Home zone: {preferences.home_zone}\n"
        f"Reminders: {preferences.reminder_calendar_days} calendar day(s) and "
        f"{preferences.reminder_elapsed_hours} elapsed hour(s) before\n"
        f"Quiet hours: {_clock(preferences.quiet_start.isoformat())}–"
        f"{_clock(preferences.quiet_end.isoformat())}\n"
        f"Evening review: {_clock(preferences.review_time.isoformat())}"
    )


def _clock(value: str) -> str:
    return time.fromisoformat(value).strftime("%H:%M")


def _int_value(value: object, default: int) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _reply(
    source: str, *, handling: str = "handled", buttons: tuple[OnboardingButton, ...] = ()
) -> OnboardingReply:
    plain_text = re.sub(
        r"`([^`]*)`|\*\*(.*?)\*\*",
        lambda match: match.group(1) or match.group(2),
        source,
    )
    markdown_parts = re.split(r"(`[^`]*`|\*\*.*?\*\*)", source)
    markdown: list[str] = []
    for part in markdown_parts:
        if part.startswith("`") and part.endswith("`"):
            code = part[1:-1].replace("\\", "\\\\").replace("`", "\\`")
            markdown.append(f"`{code}`")
        elif part.startswith("**") and part.endswith("**"):
            markdown.append(f"*{escape_markdown_v2(part[2:-2])}*")
        else:
            markdown.append(escape_markdown_v2(part))
    return OnboardingReply("".join(markdown), plain_text, handling, buttons)
