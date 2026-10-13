"""Telegram delivery helpers with narrow format fallback and live typing status."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, ReplyMarkupUnion

if TYPE_CHECKING:
    from aiogram import Bot


def is_confirmed_format_rejection(error: BaseException) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False
    description = str(error).casefold()
    return "can't parse entities" in description or "can't find end of the entity" in description


async def send_formatted_message(
    bot: Bot,
    *,
    chat_id: int,
    markdown: str,
    plain_text: str,
    reply_markup: ReplyMarkupUnion | None = None,
) -> Message:
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=markdown,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as error:
        if not is_confirmed_format_rejection(error):
            raise
    return await bot.send_message(
        chat_id=chat_id,
        text=plain_text,
        parse_mode=None,
        reply_markup=reply_markup,
    )


async def _keep_typing(bot: Bot, chat_id: int, interval: float) -> None:
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            # Typing is best-effort and must never fail the actual operation.
            pass
        await asyncio.sleep(interval)


@asynccontextmanager
async def typing_status(bot: Bot, chat_id: int, *, interval: float = 4.0) -> AsyncIterator[None]:
    """Show typing only while the caller's real asynchronous work is active."""
    if interval <= 0:
        raise ValueError("interval must be positive")
    task = asyncio.create_task(_keep_typing(bot, chat_id, interval))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
