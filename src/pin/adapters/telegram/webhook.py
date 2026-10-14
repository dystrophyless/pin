"""Bounded, secret-authenticated Telegram webhook adapter."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime

from aiogram import Bot
from aiohttp import web
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pin.adapters.telegram.ingress import (
    PostgresTelegramOwnerResolver,
    TelegramOwnerResolver,
    acknowledge_callback,
    persist_update,
    select_ingress_mode,
    validate_update,
)
from pin.adapters.telegram.payments import (
    PRECHECKOUT_ANSWER_RESERVE_SECONDS,
    PRECHECKOUT_DEADLINE_SECONDS,
    PreCheckoutOutcomeUnknown,
    answer_pre_checkout,
    pre_checkout_query,
)

MAX_WEBHOOK_BODY_BYTES = 256 * 1024
WEBHOOK_PATH = "/telegram/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def create_webhook_app(
    *,
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    bot: Bot,
    bot_id: int,
    webhook_secret: str,
    owner_resolver: TelegramOwnerResolver | None = None,
) -> web.Application:
    resolver = owner_resolver or PostgresTelegramOwnerResolver()
    app = web.Application(client_max_size=MAX_WEBHOOK_BODY_BYTES)

    async def prepare_ingress(_application: web.Application) -> None:
        async with sessions() as session, session.begin():
            await select_ingress_mode(session, bot_id=bot_id, mode="webhook")

    async def close_resources(_application: web.Application) -> None:
        await bot.session.close()
        await engine.dispose()

    async def receive(request: web.Request) -> web.Response:
        received_at = asyncio.get_running_loop().time()
        if not secrets.compare_digest(request.headers.get(SECRET_HEADER, ""), webhook_secret):
            raise web.HTTPUnauthorized()
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            raw = await request.read()
            if len(raw) > MAX_WEBHOOK_BODY_BYTES:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=MAX_WEBHOOK_BODY_BYTES,
                    actual_size=len(raw),
                )
            payload: object = json.loads(raw)
            update = validate_update(payload, bot)
        except web.HTTPRequestEntityTooLarge:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(text="invalid Telegram update") from None

        try:
            if pre_checkout_query(update) is not None:
                persist_budget = (
                    received_at
                    + PRECHECKOUT_DEADLINE_SECONDS
                    - asyncio.get_running_loop().time()
                    - PRECHECKOUT_ANSWER_RESERVE_SECONDS
                )
                if persist_budget <= 0:
                    raise TimeoutError
                async with asyncio.timeout(persist_budget):
                    async with sessions() as session, session.begin():
                        receipt = await persist_update(
                            session,
                            bot_id=bot_id,
                            payload=update,
                            owner_resolver=resolver,
                        )
            else:
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=update,
                        owner_resolver=resolver,
                    )
        except TimeoutError:
            raise web.HTTPServiceUnavailable(text="payment ingress persistence timed out") from None
        except SQLAlchemyError:
            raise web.HTTPServiceUnavailable(text="durable ingress unavailable") from None

        if receipt is not None and receipt.callback_query_id is not None:
            await acknowledge_callback(bot, receipt.callback_query_id)
        if pre_checkout_query(update) is not None:
            try:
                await answer_pre_checkout(
                    sessions,
                    bot,
                    bot_id=bot_id,
                    update=update,
                    now=datetime.now(UTC),
                    received_at=received_at,
                )
            except PreCheckoutOutcomeUnknown:
                raise web.HTTPServiceUnavailable(text="payment decision outcome unknown") from None
        return web.json_response({"ok": True})

    app.router.add_post(WEBHOOK_PATH, receive)
    app.on_startup.append(prepare_ingress)
    app.on_cleanup.append(close_resources)
    return app
