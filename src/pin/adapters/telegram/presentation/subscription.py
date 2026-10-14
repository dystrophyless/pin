"""Deterministic access and allowance copy from the current Billing snapshot."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import OnboardingReply
from pin.contexts.billing.api import AccountLifecycle, PeriodKind, SubscriptionStatus


def render_subscription_status(
    status: SubscriptionStatus, *, language: str, zone_name: str
) -> OnboardingReply:
    russian = language == "ru"
    if status.account_lifecycle is AccountLifecycle.DELETED:
        text = (
            "Аккаунт удалён. Для возвращения используйте /start."
            if russian
            else "Your account is deleted. Use /start to return."
        )
    elif status.active:
        trial = status.period_kind is PeriodKind.TRIAL
        heading = (
            ("Пробный доступ активен" if trial else "Подписка активна")
            if russian
            else ("Trial access is active" if trial else "Subscription is active")
        )
        lines = [heading]
        if status.ends_at is not None:
            end = _local_end(status.ends_at, zone_name)
            lines.append(f"Действует до: {end}" if russian else f"Available until: {end}")
        turns = "—" if status.ai_turns_remaining is None else str(status.ai_turns_remaining)
        voice = _voice_remaining(status.voice_seconds_remaining, russian=russian)
        lines.extend(
            (
                f"Осталось ИИ-запросов: {turns}" if russian else f"AI turns remaining: {turns}",
                f"Осталось голоса: {voice}" if russian else f"Voice remaining: {voice}",
                (
                    "Напоминания работают независимо от лимитов ИИ и голоса."
                    if russian
                    else "Reminders continue independently of AI and voice allowances."
                ),
            )
        )
        text = "\n".join(lines)
    else:
        lines = ["Активного доступа нет." if russian else "There is no active access."]
        if status.ends_at is not None:
            end = _local_end(status.ends_at, zone_name)
            lines.append(f"Период: до {end}" if russian else f"Period: until {end}")
        if status.trial_used:
            lines.append(
                "Пробный период уже использован." if russian else "Your trial was already used."
            )
        lines.append(
            "Просмотр, выполнение и отмена планов доступны. Для новых планов, ИИ, голоса "
            "и напоминаний нужна активная подписка."
            if russian
            else "You can still view, complete and cancel plans. An active subscription is needed "
            "for new plans, AI, voice and reminders."
        )
        text = "\n".join(lines)
    return OnboardingReply(escape_markdown_v2(text), text)


def render_access_expiry_notice(
    *, period_kind: PeriodKind, ends_at: datetime, language: str, zone_name: str
) -> OnboardingReply:
    end = _local_end(ends_at, zone_name)
    if language == "ru":
        kind = "Пробный доступ" if period_kind is PeriodKind.TRIAL else "Подписка"
        text = (
            f"{kind}: до {end}.\n"
            "После окончания доступ к новым планам, ИИ, голосу и напоминаниям приостанавливается. "
            "Просмотр, выполнение и отмена планов остаются доступны.\n"
            "Текущий доступ и лимиты: /subscription"
        )
    else:
        kind = "Trial access" if period_kind is PeriodKind.TRIAL else "Subscription"
        text = (
            f"{kind}: until {end}.\n"
            "After expiry, new plans, AI, voice and reminders pause. "
            "Viewing, completing and cancelling plans remain available.\n"
            "Current access and allowances: /subscription"
        )
    return OnboardingReply(escape_markdown_v2(text), text)


def _local_end(value: datetime, zone_name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("access end must be timezone-aware")
    return f"{value.astimezone(ZoneInfo(zone_name)):%d.%m.%Y %H:%M} ({zone_name})"


def _voice_remaining(seconds: int | None, *, russian: bool) -> str:
    if seconds is None:
        return "—"
    minutes, remainder = divmod(max(0, seconds), 60)
    return f"{minutes} мин {remainder} с" if russian else f"{minutes} min {remainder} sec"
