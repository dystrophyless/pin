"""Bounded, secret-authenticated Telegram webhook adapter."""

from __future__ import annotations

import json
import secrets

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
            async with sessions() as session, session.begin():
                receipt = await persist_update(
                    session,
                    bot_id=bot_id,
                    payload=update,
                    owner_resolver=resolver,
                )
        except SQLAlchemyError:
            raise web.HTTPServiceUnavailable(text="durable ingress unavailable") from None

        if receipt is not None and receipt.callback_query_id is not None:
            await acknowledge_callback(bot, receipt.callback_query_id)
        return web.json_response({"ok": True})

    app.router.add_post(WEBHOOK_PATH, receive)
    app.on_startup.append(prepare_ingress)
    app.on_cleanup.append(close_resources)
    return app
