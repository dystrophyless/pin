from __future__ import annotations

import argparse
import logging
import os
import socket

from aiogram import Bot

from pin.adapters.telegram.delivery_worker import run_delivery_worker
from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.observability import configure_logging
from pin.persistence import create_engine, create_session_factory, run_local_async

logger = logging.getLogger("pin.runtime")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-delivery-worker")
    parser.add_argument("--check", action="store_true", help="validate configuration only")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(ProcessRole.DELIVERY_WORKER)
    except SettingsError as error:
        logger.error("startup_rejected: %s", error)
        raise SystemExit(2) from None
    logger.info("startup_ready: role=%s", ProcessRole.DELIVERY_WORKER.value)
    if args.check:
        return
    token = settings.bot_token
    if token is None:
        raise SystemExit(2)
    bot = Bot(token=token.get_secret_value())
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = create_session_factory(engine)
    worker_id = f"delivery-{socket.gethostname()}-{os.getpid()}"

    async def run() -> None:
        try:
            await run_delivery_worker(
                sessions, TelegramReminderTransport(bot), worker_id=worker_id
            )
        finally:
            await bot.session.close()
            await engine.dispose()

    try:
        run_local_async(run)
    except Exception as error:
        logger.error("worker_stopped: error=%s", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

