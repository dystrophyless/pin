from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys

from aiogram import Bot
from aiohttp import web

from pin.adapters.telegram.polling import poll_updates
from pin.adapters.telegram.webhook import create_webhook_app
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.observability import configure_logging, log_startup_configuration_rejection
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.worker_heartbeat import worker_heartbeat

logger = logging.getLogger("pin.runtime")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-gateway")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration without connecting to external systems",
    )
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(ProcessRole.GATEWAY)
    except SettingsError as error:
        log_startup_configuration_rejection(
            logger, error.diagnostic_category, error.diagnostic_fields
        )
        raise SystemExit(2) from None
    logger.info("startup_ready: role=%s", ProcessRole.GATEWAY.value)
    if args.check:
        return

    token = settings.bot_token
    if token is None:
        raise SystemExit(2)
    bot = Bot(token=token.get_secret_value())
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    bot_id = int(token.get_secret_value().partition(":")[0])
    worker_id = f"gateway-{socket.gethostname()}-{os.getpid()}"

    if settings.telegram_ingress_mode == "polling":

        async def run_poller() -> None:
            try:
                async with worker_heartbeat(sessions, worker_id=worker_id, role="gateway"):
                    await poll_updates(
                        engine=engine,
                        sessions=sessions,
                        bot=bot,
                        bot_id=bot_id,
                    )
            finally:
                await bot.session.close()
                await engine.dispose()

        try:
            run_local_async(run_poller)
        except Exception as error:
            logger.error("gateway_stopped: error=%s", type(error).__name__)
            raise SystemExit(1) from None
        return

    webhook_secret = settings.webhook_secret
    if webhook_secret is None:
        raise SystemExit(2)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    web.run_app(
        create_webhook_app(
            engine=engine,
            sessions=sessions,
            bot=bot,
            bot_id=bot_id,
            webhook_secret=webhook_secret.get_secret_value(),
        ),
        host=settings.gateway_host,
        port=settings.gateway_port,
    )


if __name__ == "__main__":
    main()
