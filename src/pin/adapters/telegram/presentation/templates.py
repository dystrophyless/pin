"""Deterministic Russian and English help/support copy."""

from __future__ import annotations

from pin.adapters.telegram.presentation.formatting import escape_markdown_v2

_HELP = {
    "en": (
        "Pin helps you keep track of plans.\n"
        "/today — today's plans\n"
        "/week — this week's plans\n"
        "/settings — preferences\n"
        "/subscription — access and allowance\n"
        "/privacy — privacy information\n"
        "/support — contact support"
    ),
    "ru": (
        "Pin помогает следить за планами.\n"
        "/today — планы на сегодня\n"
        "/week — планы на эту неделю\n"
        "/settings — настройки\n"
        "/subscription — доступ и лимиты\n"
        "/privacy — конфиденциальность\n"
        "/support — связь с поддержкой"
    ),
}
_SUPPORT = {
    "en": "Contact support: {contact}",
    "ru": "Связь с поддержкой: {contact}",
}
_PRIVACY = {
    "en": (
        "Pin is an independent planning service, separate from Telegram.\n"
        "We receive your Telegram ID and the data Telegram sends with messages, your "
        "preferences, submitted text and voice messages, plans, and payment facts.\n"
        "For interpretation, Pin sends submitted text, voice transcripts, and relevant "
        "context from your Pin plans through OpenRouter to the selected model provider. "
        "Voice is transcribed locally on Pin's server; transcript text may still be sent "
        "to that provider.\n"
        "Pin deletes its audio copies after processing; crash leftovers are deleted "
        "within 24 hours. Operational message text is kept for 30 days, plans until "
        "confirmed account deletion, and backups for up to 7 days.\n"
        "Telegram and the selected model provider handle copies under their own policies. "
        "Pin's local deletion periods do not apply to their copies. Pin makes no promise "
        "about provider retention or model training.\n"
        "After /delete_account, Pin shows a preview and asks you to confirm. To prevent "
        "trial resets and restore currently paid access, Pin may retain minimal facts "
        "about prior trial use and current paid coverage. Minimized payment records are "
        "kept separately; the applicable statutory period has not been verified.\n"
        "Reading this notice does not itself authorize sharing your data with the selected "
        "model provider. For data questions, use /support."
    ),
    "ru": (
        "Pin — самостоятельный сервис планирования, отдельный от Telegram.\n"
        "Мы получаем ваш Telegram ID и данные, которые Telegram передаёт вместе с "
        "сообщениями, ваши настройки, отправленные текстовые и голосовые сообщения, "
        "планы и сведения об оплатах.\n"
        "Для интерпретации Pin передаёт отправленный текст, расшифровки голоса и "
        "релевантный контекст ваших планов через OpenRouter выбранному поставщику "
        "модели. Голос распознаётся локально на сервере Pin; распознанный текст всё "
        "равно может передаваться этому поставщику.\n"
        "Pin удаляет свои копии аудио после обработки; оставшиеся после сбоя файлы "
        "удаляются в течение 24 часов. Текст сообщений для работы хранится 30 дней, "
        "планы — до подтверждённого удаления аккаунта, резервные копии — до 7 дней.\n"
        "Telegram и выбранный поставщик модели обрабатывают свои копии по собственным "
        "правилам. Сроки удаления Pin не распространяются на эти копии. Pin не обещает "
        "срок хранения данных поставщиком или отсутствие обучения моделей на них.\n"
        "Команда /delete_account показывает предварительный просмотр и запрашивает "
        "подтверждение. Для защиты от повторного пробного периода и восстановления "
        "текущего оплаченного доступа Pin может сохранить минимум сведений о прежнем "
        "пробном периоде и текущем оплачиваемом доступе. Минимизированные платёжные "
        "записи хранятся отдельно; обязательный по закону срок пока не подтверждён.\n"
        "Чтение этого сообщения само по себе не означает согласия на передачу ваших "
        "данных выбранному поставщику модели. По вопросам обработки данных используйте "
        "/support."
    ),
}


def render_privacy(language: str) -> str:
    return escape_markdown_v2(plain_privacy(language))


def plain_privacy(language: str) -> str:
    try:
        return _PRIVACY[language]
    except KeyError as error:
        raise ValueError("unsupported language") from error


def render_help(language: str) -> str:
    return escape_markdown_v2(plain_help(language))


def plain_help(language: str) -> str:
    try:
        return _HELP[language]
    except KeyError as error:
        raise ValueError("unsupported language") from error


def render_support(language: str, contact: str | None) -> str:
    return escape_markdown_v2(plain_support(language, contact))


def plain_support(language: str, contact: str | None) -> str:
    if (
        not contact
        or not contact.strip()
        or any(char.isspace() for char in contact.strip())
        or "placeholder" in contact.casefold()
    ):
        raise ValueError("support destination is not configured")
    try:
        template = _SUPPORT[language]
    except KeyError as error:
        raise ValueError("unsupported language") from error
    return template.format(contact=contact.strip())
