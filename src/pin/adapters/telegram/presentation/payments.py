"""Localized Telegram Stars terms and support responses."""

from __future__ import annotations

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2
from pin.adapters.telegram.presentation.onboarding import OnboardingReply
from pin.contexts.billing.domain.payments import STARS_PRICE


def render_payment_terms(language: str) -> OnboardingReply:
    russian = language == "ru"
    text = (
        f"Подписка Pin стоит {STARS_PRICE} Telegram Stars за 30 дней и автоматически продлевается. "
        "Первая оплаченная дата и окончание периода определяются подтверждением Telegram. "
        "Команда /cancel отключает будущие продления; оплаченный период сохраняется до конца. "
        "Для каждого оплаченного периода доступны 1 000 обращений к ИИ и "
        "60 минут обработки голоса, "
        "без переноса остатка. Возврат рассматривается оператором вручную через /paysupport. "
        "Прочитайте условия и отправьте /acceptterms, чтобы разрешить создание заказа."
        if russian
        else (
            f"Pin costs {STARS_PRICE} Telegram Stars for each 30-day period and "
            "renews automatically. "
            "Telegram's confirmed payment defines the paid period dates. "
            "Use /cancel to stop future renewals; your paid access remains through "
            "its current end date. "
            "Each paid period includes 1,000 AI turns and 60 minutes of voice processing, "
            "with no rollover. Refunds are reviewed manually by an operator through /paysupport. "
            "Read these terms, then send /acceptterms to allow an order."
        )
    )
    return OnboardingReply(escape_markdown_v2(text), text)


def render_terms_accepted(language: str) -> OnboardingReply:
    text = (
        "Условия приняты. Отправьте /buy, чтобы создать заказ и открыть счёт на оплату."
        if language == "ru"
        else "Terms accepted. Send /buy to create an order and open the Telegram invoice."
    )
    return OnboardingReply(escape_markdown_v2(text), text)


def render_payment_support(language: str, contact: str | None) -> OnboardingReply:
    if contact and (not contact.strip() or any(char.isspace() for char in contact.strip())):
        contact = None
    if contact and language == "ru":
        text = (
            f"Поддержка платежей: {contact}. Укажите Telegram charge ID из квитанции. "
            "Возвраты рассматривает оператор вручную."
        )
    elif contact:
        text = (
            f"Payment support: {contact}. Include the Telegram charge ID from the receipt. "
            "Refunds are reviewed manually."
        )
    elif language == "ru":
        text = (
            "По вопросам оплаты обратитесь к оператору бота и укажите Telegram charge ID "
            "из квитанции. Не отправляйте банковские данные. Возвраты рассматриваются вручную."
        )
    else:
        text = (
            "For payment help, contact the bot operator with the Telegram charge ID from "
            "your receipt. Do not send bank details. Refunds are reviewed manually."
        )
    return OnboardingReply(escape_markdown_v2(text), text)


def render_payment_result(language: str, *, accepted: bool) -> OnboardingReply:
    text = (
        "Оплата подтверждена Telegram. Период доступа и квоты обновлены."
        if language == "ru" and accepted
        else "Telegram confirmed payment. Your access period and quotas are updated."
        if accepted
        else "Оплату не удалось подтвердить. Доступ не изменён; обратитесь в /paysupport."
        if language == "ru"
        else "The payment could not be verified. Access was not changed; use /paysupport."
    )
    return OnboardingReply(escape_markdown_v2(text), text)


def render_cancel_result(language: str, status: str) -> OnboardingReply:
    text = (
        "Продление отключено. Оплаченный период остаётся доступен до указанной даты."
        if language == "ru" and status == "cancelled"
        else "Renewal is already disabled. Paid access remains available through its end date."
        if status == "cancelled"
        else "Запрос на отключение продления отправлен. Проверьте статус через /subscription."
        if language == "ru"
        else "The renewal cancellation was queued. Check /subscription for its confirmed status."
    )
    return OnboardingReply(escape_markdown_v2(text), text)
