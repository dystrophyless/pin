from __future__ import annotations

import argparse
import logging
import os
import socket

from aiogram import Bot

from pin.adapters.telegram.delivery_worker import run_delivery_worker
from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.observability import configure_logging, log_startup_configuration_rejection
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.recovery import RecoveryFenced, require_runtime_recovery_ready
from pin.platform.worker_heartbeat import worker_heartbeat

logger = logging.getLogger("pin.runtime")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-delivery-worker")
    parser.add_argument("--check", action="store_true", help="validate configuration only")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(ProcessRole.DELIVERY_WORKER)
    except SettingsError as error:
        log_startup_configuration_rejection(
            logger, error.diagnostic_category, error.diagnostic_fields
        )
        raise SystemExit(2) from None
    if not args.check:
        try:
            require_runtime_recovery_ready(settings.database_url.get_secret_value())
        except RecoveryFenced:
            logger.error("startup_rejected: reason=recovery_gate")
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
            async with worker_heartbeat(sessions, worker_id=worker_id, role="delivery"):
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
