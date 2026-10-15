"""Bounded, secret-authenticated Telegram webhook adapter."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime

from aiogram import Bot
from aiohttp import web
from sqlalchemy import text
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
READINESS_TIMEOUT_SECONDS = 2.0
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

    async def liveness(_request: web.Request) -> web.Response:
        return web.json_response({"status": "alive"})

    async def readiness(_request: web.Request) -> web.Response:
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
                async with sessions() as session:
                    await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, TimeoutError):
            return web.json_response({"status": "not_ready", "dependency": "database"}, status=503)
        return web.json_response({"status": "ready"})

    async def receive(request: web.Request) -> web.Response:
        received_at = datetime.now(UTC)
        received_monotonic = asyncio.get_running_loop().time()
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
                    received_monotonic
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
                            received_at=received_at,
                        )
            else:
                async with sessions() as session, session.begin():
                    receipt = await persist_update(
                        session,
                        bot_id=bot_id,
                        payload=update,
                        owner_resolver=resolver,
                        received_at=received_at,
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
                    received_at=received_monotonic,
                )
            except PreCheckoutOutcomeUnknown:
                raise web.HTTPServiceUnavailable(text="payment decision outcome unknown") from None
        return web.json_response({"ok": True})

    app.router.add_post(WEBHOOK_PATH, receive)
    app.router.add_get("/healthz", liveness)
    app.router.add_get("/readyz", readiness)
    app.on_startup.append(prepare_ingress)
    app.on_cleanup.append(close_resources)
    return app
