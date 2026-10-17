from __future__ import annotations

import argparse
import logging
import os
import socket
from datetime import UTC, datetime

from aiogram import Bot

from pin.adapters.telegram.speech_worker import run_speech_worker
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.bootstrap.speech import require_speech_runtime
from pin.contexts.conversation.application.service import (
    CAPTURE_MAX_OUTPUT_TOKENS,
    BoundedPlanningAssistant,
)
from pin.contexts.conversation.infrastructure.openrouter import OpenRouterTransport
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.observability import (
    configure_logging,
    log_speech_dependencies_missing,
    log_startup_configuration_rejection,
)
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.recovery import RecoveryFenced, require_runtime_recovery_ready
from pin.platform.worker_heartbeat import worker_heartbeat

logger = logging.getLogger("pin.runtime")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-speech-worker")
    parser.add_argument("--check", action="store_true", help="validate configuration only")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(ProcessRole.SPEECH_WORKER)
        require_speech_runtime()
    except (SettingsError, RuntimeError) as error:
        if isinstance(error, SettingsError):
            log_startup_configuration_rejection(
                logger, error.diagnostic_category, error.diagnostic_fields
            )
        else:
            log_speech_dependencies_missing(logger)
        raise SystemExit(2) from None
    if not args.check:
        try:
            require_runtime_recovery_ready(settings.database_url.get_secret_value())
        except RecoveryFenced:
            logger.error("startup_rejected: reason=recovery_gate")
            raise SystemExit(2) from None
    logger.info("startup_ready: role=%s", ProcessRole.SPEECH_WORKER.value)
    if args.check:
        return
    assert settings.bot_token is not None and settings.openrouter_api_key is not None
    bot = Bot(token=settings.bot_token.get_secret_value())
    provider = OpenRouterTransport(api_key=settings.openrouter_api_key)
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = create_session_factory(engine)
    assistant = BoundedPlanningAssistant(
        transport=provider,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: datetime.now(UTC),
        max_output_tokens=CAPTURE_MAX_OUTPUT_TOKENS,
    )
    worker_id = f"speech-{socket.gethostname()}-{os.getpid()}"

    async def run() -> None:
        try:
            async with worker_heartbeat(sessions, worker_id=worker_id, role="speech"):
                await run_speech_worker(sessions, worker_id=worker_id, bot=bot, assistant=assistant)
        finally:
            await bot.session.close()
            await provider.aclose()
            await engine.dispose()

    try:
        run_local_async(run)
    except Exception as error:
        logger.error("worker_stopped: error=%s", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
