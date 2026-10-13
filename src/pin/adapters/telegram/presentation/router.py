"""Small private-chat router for the presentation foundation's own commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from pin.adapters.telegram.presentation.delivery import send_formatted_message
from pin.adapters.telegram.presentation.templates import (
    plain_help,
    plain_support,
    render_help,
    render_support,
)

LanguageResolver = Callable[[int], Awaitable[str]]


def create_help_support_router(
    *, support_contact: str | None, language_for_telegram_user: LanguageResolver
) -> Router:
    """Build the deterministic /help and /support routes; missing support fails at setup."""
    if (
        not support_contact
        or not support_contact.strip()
        or any(char.isspace() for char in support_contact.strip())
        or "placeholder" in support_contact.casefold()
    ):
        raise ValueError("support destination is not configured")

    router = Router(name="telegram-presentation")

    async def reply(message: Message, rendered: str, plain_text: str) -> None:
        if message.chat.type != "private" or message.from_user is None:
            return
        bot = message.bot
        if bot is None:
            raise RuntimeError("Telegram Bot context is required for command replies")
        await send_formatted_message(
            bot,
            chat_id=message.chat.id,
            markdown=rendered,
            plain_text=plain_text,
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if message.chat.type != "private" or message.from_user is None:
            return
        language = await language_for_telegram_user(message.from_user.id)
        await reply(message, render_help(language), plain_help(language))

    @router.message(Command("support"))
    async def support_command(message: Message) -> None:
        if message.chat.type != "private" or message.from_user is None:
            return
        language = await language_for_telegram_user(message.from_user.id)
        await reply(
            message,
            render_support(language, support_contact),
            plain_support(language, support_contact),
        )

    return router
