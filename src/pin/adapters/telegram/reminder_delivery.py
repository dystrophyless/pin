"""Telegram transport for durable reminder and private reply delivery."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.adapters.telegram.ingress import TelegramOwnerRecord
from pin.adapters.telegram.presentation.delivery import send_formatted_message
from pin.contexts.notifications.application.delivery import (
    KnownDeliveryFailure,
    ReminderSend,
    TelegramReplySend,
    UnknownDeliveryFailure,
)


class TelegramReminderTransport:
    """Use only the current private identity mapping for a reminder destination."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def resolve_destination(self, session: AsyncSession, owner_id: UUID) -> int | None:
        target_id = await session.scalar(
            select(TelegramOwnerRecord.telegram_user_id).where(
                TelegramOwnerRecord.owner_id == owner_id,
                TelegramOwnerRecord.bot_id == self._bot.id,
            )
        )
        return int(target_id) if target_id is not None else None

    async def send_reminder(self, message: ReminderSend) -> str:
        try:
            result = await self._bot.send_message(
                chat_id=message.destination_id,
                text=message.text,
                parse_mode=None,
            )
        except TelegramRetryAfter as error:
            raise KnownDeliveryFailure(
                "rate_limited", retry_after=timedelta(seconds=error.retry_after)
            ) from error
        except TelegramForbiddenError as error:
            raise KnownDeliveryFailure("destination_forbidden", blocked=True) from error
        except TelegramBadRequest as error:
            raise KnownDeliveryFailure("provider_rejected_message") from error
        except Exception as error:
            raise UnknownDeliveryFailure("telegram_transport_unknown") from error
        return str(result.message_id)

    async def send_reply(self, message: TelegramReplySend) -> str:
        try:
            markup = _parse_markup(message.reply_markup)
            if message.parse_mode is None:
                result = await self._bot.send_message(
                    chat_id=message.destination_id,
                    text=message.plain_text,
                    parse_mode=None,
                    reply_markup=markup,
                )
            else:
                result = await send_formatted_message(
                    self._bot,
                    chat_id=message.destination_id,
                    markdown=message.markdown,
                    plain_text=message.plain_text,
                    reply_markup=markup,
                )
        except TelegramRetryAfter as error:
            raise KnownDeliveryFailure(
                "rate_limited", retry_after=timedelta(seconds=error.retry_after)
            ) from error
        except TelegramForbiddenError as error:
            raise KnownDeliveryFailure("destination_forbidden", blocked=True) from error
        except TelegramBadRequest as error:
            raise KnownDeliveryFailure("provider_rejected_message") from error
        except KnownDeliveryFailure:
            raise
        except Exception as error:
            raise UnknownDeliveryFailure("telegram_transport_unknown") from error
        return str(result.message_id)


def _parse_markup(
    payload: dict[str, object] | None,
) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None:
    if payload is None:
        return None
    try:
        if "inline_keyboard" in payload:
            return InlineKeyboardMarkup.model_validate(payload)
        if payload.get("remove_keyboard") is True:
            return ReplyKeyboardRemove.model_validate(payload)
        return ReplyKeyboardMarkup.model_validate(payload)
    except Exception as error:
        raise KnownDeliveryFailure("reply_markup_invalid") from error
