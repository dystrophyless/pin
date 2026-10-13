"""Exclusive local Telegram long polling with commit-before-offset persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from logging import getLogger

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramIngressBotRecord,
    TelegramOwnerResolver,
    acknowledge_callback,
    persist_update,
    select_ingress_mode,
    validate_update,
)

logger = getLogger("pin.telegram.polling")
ALLOWED_UPDATES = ["message", "callback_query", "pre_checkout_query"]
POLL_TIMEOUT_SECONDS = 25


async def poll_updates(
    *,
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    bot: Bot,
    bot_id: int,
    owner_resolver: TelegramOwnerResolver | None = None,
    stop: asyncio.Event | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    resolver = owner_resolver or PostgresTelegramOwnerResolver()
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:bot_id)"), {"bot_id": bot_id}
        )
        await lock_connection.commit()
        if not locked:
            raise RuntimeError("another local poller already owns this Telegram bot")

        try:
            async with sessions() as session, session.begin():
                record = await select_ingress_mode(session, bot_id=bot_id, mode="polling")
                offset = record.next_update_offset or 0

            while stop is None or not stop.is_set():
                try:
                    updates = await bot.get_updates(
                        offset=offset,
                        timeout=POLL_TIMEOUT_SECONDS,
                        allowed_updates=ALLOWED_UPDATES,
                    )
                except (TelegramNetworkError, TelegramServerError) as error:
                    logger.error("telegram_poll_request_failed error=%s", type(error).__name__)
                    await sleep(1)
                    continue
                retry_batch = False
                for update in updates:
                    payload = validate_update(update, bot)
                    update_id = payload.get("update_id")
                    if (
                        isinstance(update_id, bool)
                        or not isinstance(update_id, int)
                        or update_id < 0
                    ):
                        raise RuntimeError("Telegram returned an invalid update identifier")
                    next_offset = update_id + 1
                    callback_id: str | None = None
                    try:
                        async with sessions() as session, session.begin():
                            locked_record = await session.scalar(
                                select(TelegramIngressBotRecord)
                                .where(TelegramIngressBotRecord.bot_id == bot_id)
                                .with_for_update()
                            )
                            if locked_record is None or locked_record.mode != "polling":
                                raise RuntimeError("Telegram polling mode is no longer active")
                            receipt = await persist_update(
                                session,
                                bot_id=bot_id,
                                payload=payload,
                                owner_resolver=resolver,
                            )
                            if receipt is not None:
                                callback_id = receipt.callback_query_id
                            if callback_id is None:
                                locked_record.next_update_offset = max(
                                    locked_record.next_update_offset or 0, next_offset
                                )
                    except SQLAlchemyError:
                        logger.error("telegram_poll_persist_failed")
                        retry_batch = True
                        break
                    if callback_id is not None:
                        await acknowledge_callback(bot, callback_id)
                        async with sessions() as session, session.begin():
                            locked_record = await session.scalar(
                                select(TelegramIngressBotRecord)
                                .where(TelegramIngressBotRecord.bot_id == bot_id)
                                .with_for_update()
                            )
                            if locked_record is None or locked_record.mode != "polling":
                                raise RuntimeError("Telegram polling mode is no longer active")
                            locked_record.next_update_offset = max(
                                locked_record.next_update_offset or 0, next_offset
                            )
                    offset = next_offset

                if retry_batch:
                    await sleep(1)
        finally:
            await lock_connection.scalar(
                text("SELECT pg_advisory_unlock(:bot_id)"), {"bot_id": bot_id}
            )
            await lock_connection.commit()
