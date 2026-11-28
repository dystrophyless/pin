"""Bot API widget methods missing from the repository's locked aiogram release."""

from __future__ import annotations

from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message


class SendWidget(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendWidget"

    chat_id: int
    html: str
    caption: str | None = None
    reply_markup: InlineKeyboardMarkup | None = None
    allow_queries: bool | None = None


class AnswerWidgetQuery(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "answerWidgetQuery"

    widget_query_id: str
    data: str | None = None
