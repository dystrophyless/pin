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
        "/support — contact support"
    ),
    "ru": (
        "Pin помогает следить за планами.\n"
        "/today — планы на сегодня\n"
        "/week — планы на эту неделю\n"
        "/settings — настройки\n"
        "/subscription — доступ и лимиты\n"
        "/support — связь с поддержкой"
    ),
}
_SUPPORT = {
    "en": "Contact support: {contact}",
    "ru": "Связь с поддержкой: {contact}",
}


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
