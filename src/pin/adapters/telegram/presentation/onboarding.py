"""Deterministic RU/EN onboarding and preference-preview rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.contexts.billing.api import TrialActivationResult
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


def render_onboarding(result: OnboardingResult) -> OnboardingReply | None:
    if result.state in {"cancelled", "not_in_progress", "version_conflict", "draft_conflict"}:
        language = result.preferences.language if result.preferences else _language(result.draft)
        if result.state == "cancelled":
            if result.mode == "preferences" or result.preferences is not None:
                text = {
                    "en": "Preview cancelled. Saved preferences and access are unchanged.",
                    "ru": "Предпросмотр отменён. Сохранённые настройки и доступ не изменились.",
                }[language]
            else:
                text = {
                    "en": (
                        "Onboarding was cancelled. No preferences were saved and "
                        "no trial was started."
                    ),
                    "ru": "Настройка отменена. Параметры не сохранены, пробный период не начат.",
                }[language]
        else:
            text = {
                "en": {
                    "not_in_progress": "Send /start to set up your preferences.",
                    "version_conflict": (
                        "Preferences changed in another session. Send /settings to "
                        "review the current version."
                    ),
                    "draft_conflict": (
                        "This setup changed in another session. Send /settings to "
                        "load the latest version."
                    ),
                },
                "ru": {
                    "not_in_progress": "Отправьте /start, чтобы настроить параметры.",
                    "version_conflict": (
                        "Настройки изменились в другой сессии. Отправьте /settings, "
                        "чтобы проверить текущую версию."
                    ),
                    "draft_conflict": (
                        "Эта настройка изменилась в другой сессии. Отправьте /settings, "
                        "чтобы загрузить последнюю версию."
                    ),
                },
            }[language][result.state]
        return _reply(text)
    if result.state in {"existing_profile", "already_complete"} and result.preferences:
        language = result.preferences.language
        return _reply(
            _profile_text(result.preferences),
            buttons=(_button(language, "settings", "settings"),),
        )
    if result.state == "unhandled":
        language = result.preferences.language if result.preferences else _language(result.draft)
        text = (
            "Создание планов пока недоступно в этой локальной версии. Сообщение не сохранено."
            if language == "ru"
            else "Plan capture is not available in this local version. Your message was not saved."
        )
        return _reply(text, handling="unhandled")

    language = _language(result.draft)
    prompts = {
        "en": {
            "started": "Choose your language: reply `en` or `ru`.",
            "resumed": "Continuing your saved setup.\n",
            "language": "Choose your conversation language: reply `en` or `ru`.",
            "home_zone": (
                "Send your home time zone as an IANA name, e.g. `Asia/Almaty`. "
                "Use the example only if it is your zone; I will not infer it from "
                "language or location."
            ),
            "reminders": (
                "Reminder suggestions: one calendar day and one elapsed hour before "
                "timed plans. Reply `suggested` or send two numbers: calendar days, "
                "elapsed hours (example: `1, 1`)."
            ),
            "quiet_review": (
                "Quiet hours and evening review suggestions: `22:00-08:00; 20:00`. "
                "Reply `suggested` or send `quiet-start quiet-end; review-time`."
            ),
            "confirm": (
                "{summary}\nReply `confirm` to save these preferences{trial}; "
                "send /cancel to leave without saving."
            ),
            "invalid_input": "That value is not valid. Please try again.",
            "confirm_required": "Reply `confirm` to save, or send /cancel.",
            "already_complete": "Your preferences are already saved.",
        },
        "ru": {
            "started": "Выберите язык: отправьте `ru` или `en`.",
            "resumed": "Продолжаем сохранённую настройку.\n",
            "language": "Выберите язык общения: отправьте `ru` или `en`.",
            "home_zone": (
                "Введите домашний часовой пояс IANA, например `Asia/Almaty`. "
                "Кнопка с примером выберет его только по вашему нажатию; язык и "
                "местоположение не используются для определения."
            ),
            "reminders": (
                "Предлагаемые напоминания: за календарный день и за час до планов "
                "со временем. Отправьте `по умолчанию` или два числа: календарные "
                "дни и часы (пример: `1, 1`)."
            ),
            "quiet_review": (
                "Предлагаемые тихие часы и время обзора: `22:00-08:00; 20:00`. "
                "Отправьте `по умолчанию` или `начало-конец; время-обзора`."
            ),
            "confirm": (
                "{summary}\nОтправьте `подтверждаю`, чтобы сохранить{trial}; "
                "отправьте /cancel, чтобы выйти без сохранения."
            ),
            "invalid_input": "Это значение не подходит. Попробуйте ещё раз.",
            "confirm_required": "Отправьте `подтверждаю`, чтобы сохранить, или /cancel.",
            "already_complete": "Ваши настройки уже сохранены.",
        },
    }[language]

    if result.state in {"started", "resumed", "advanced"}:
        if result.state in {"started", "resumed"} and result.step == "language":
            text = (
                prompts["started"]
                if result.state == "started"
                else prompts["resumed"] + prompts["language"]
            )
        elif result.step == "confirm":
            summary = _summary(result.draft or {}, language)
            trial = (
                (
                    " (if eligible: 7 days, 250 AI interactions and 15 voice minutes)"
                    if language == "en"
                    else (
                        " (если условия выполнены: 7 дней, 250 обращений к ИИ и "
                        "15 минут голосовых сообщений)"
                    )
                )
                if result.mode == "initial"
                else ""
            )
            text = prompts["confirm"].format(summary=summary, trial=trial)
        else:
            text = prompts.get(result.step or "language", prompts["language"])
        buttons = _step_buttons(language, result.step or "language", result.mode or "initial")
        if result.mode == "preferences" and result.step not in {"language", "confirm"}:
            text += (
                "\nReply `keep` to retain this current value."
                if language == "en"
                else "\nОтправьте `оставить`, чтобы сохранить текущее значение."
            )
        return _reply(text, buttons=buttons)
    if result.state in {"invalid_input", "confirm_required"}:
        return _reply(
            prompts[result.state],
            buttons=_step_buttons(language, result.step or "language", result.mode or "initial"),
        )
    if result.state == "confirmed" and result.preferences:
        language = result.preferences.language
        return _reply(
            _profile_text(result.preferences, confirmed=True, trial=result.trial),
            buttons=(_button(language, "settings", "settings"),),
        )
    return None


def _step_buttons(language: str, step: str, mode: str) -> tuple[OnboardingButton, ...]:
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
    elif step == "home_zone":
        buttons.append(_button(language, "zone_example", "answer", "home_zone|Asia/Almaty"))
    elif step == "confirm":
        buttons.append(_button(language, "confirm", "answer", "confirm|confirm"))
    if mode == "preferences" and step not in {"language", "confirm"}:
        buttons.append(_button(language, "keep", "answer", f"{step}|keep"))
    buttons.append(_button(language, "cancel", "cancel", "draft"))
    return tuple(buttons)


def _button(language: str, key: str, action: str, value: str | None = None) -> OnboardingButton:
    labels = {
        "en": {
            "en": "English",
            "ru": "Русский",
            "suggested": "Use suggestions",
            "zone_example": "Asia/Almaty (example)",
            "keep": "Keep current",
            "confirm": "Confirm",
            "cancel": "Cancel",
            "settings": "Edit preferences",
        },
        "ru": {
            "en": "English",
            "ru": "Русский",
            "suggested": "Использовать предложенное",
            "zone_example": "Asia/Almaty (пример)",
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


def _profile_text(
    preferences: PreferencesSnapshot,
    *,
    confirmed: bool = False,
    trial: TrialActivationResult | None = None,
) -> str:
    prefix = "Preferences saved." if confirmed else "Your saved preferences:"
    trial_text = _trial_text(preferences.language, trial) if confirmed else ""
    if preferences.language == "ru":
        prefix = "Настройки сохранены." if confirmed else "Сохранённые настройки:"
        return (
            f"{prefix}\nЯзык: {preferences.language}\nДомашний пояс: {preferences.home_zone}\n"
            f"Напоминания: за {preferences.reminder_calendar_days} календарный день "
            f"и {preferences.reminder_elapsed_hours} ч.\n"
            f"Тихие часы: {_clock(preferences.quiet_start.isoformat())}–"
            f"{_clock(preferences.quiet_end.isoformat())}\n"
            f"Вечерний обзор: {_clock(preferences.review_time.isoformat())}{trial_text}"
        )
    return (
        f"{prefix}\nLanguage: {preferences.language}\nHome zone: {preferences.home_zone}\n"
        f"Reminders: {preferences.reminder_calendar_days} calendar day(s) and "
        f"{preferences.reminder_elapsed_hours} elapsed hour(s) before\n"
        f"Quiet hours: {_clock(preferences.quiet_start.isoformat())}–"
        f"{_clock(preferences.quiet_end.isoformat())}\n"
        f"Evening review: {_clock(preferences.review_time.isoformat())}{trial_text}"
    )


def _trial_text(language: str, trial: TrialActivationResult | None) -> str:
    if trial is None:
        return ""
    status = trial.status.value
    messages = {
        "activated": ("\nПробный период начат.", "\nTrial started."),
        "previously_activated": (
            "\nПробный период уже использован; новый не начат.",
            "\nThe trial was already used; no new trial was started.",
        ),
        "already_active": ("\nДействующий доступ сохранён.", "\nExisting access was kept."),
    }
    return messages.get(status, ("", ""))[0 if language == "ru" else 1]


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
