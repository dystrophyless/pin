from __future__ import annotations

import argparse
import logging
import os
import socket
from datetime import UTC, datetime

from pin.adapters.telegram.application_worker import run_application_worker
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings
from pin.contexts.conversation.application.service import (
    CAPTURE_MAX_OUTPUT_TOKENS,
    BoundedPlanningAssistant,
)
from pin.contexts.conversation.infrastructure.openrouter import OpenRouterTransport
from pin.contexts.conversation.infrastructure.usage import (
    SqlAlchemyProviderUsageRecorder,
    SqlAlchemyTurnAllowance,
)
from pin.observability import configure_logging
from pin.persistence import create_engine, create_session_factory, run_local_async

logger = logging.getLogger("pin.runtime")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-app-worker")
    parser.add_argument("--check", action="store_true", help="validate configuration only")
    args = parser.parse_args()
    configure_logging()
    try:
        settings = load_settings(ProcessRole.APP_WORKER)
    except SettingsError as error:
        logger.error("startup_rejected: %s", error)
        raise SystemExit(2) from None
    logger.info("startup_ready: role=%s", ProcessRole.APP_WORKER.value)
    if args.check:
        return
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = create_session_factory(engine)
    worker_id = f"app-{socket.gethostname()}-{os.getpid()}"
    assert settings.openrouter_api_key is not None
    provider = OpenRouterTransport(api_key=settings.openrouter_api_key)
    assistant = BoundedPlanningAssistant(
        transport=provider,
        usage=SqlAlchemyProviderUsageRecorder(sessions),
        allowance=SqlAlchemyTurnAllowance(sessions),
        now=lambda: datetime.now(UTC),
        max_output_tokens=CAPTURE_MAX_OUTPUT_TOKENS,
    )

    async def run() -> None:
        try:
            await run_application_worker(
                sessions, worker_id=worker_id, assistant=assistant
            )
        finally:
            await provider.aclose()
            await engine.dispose()

    try:
        run_local_async(run)
    except Exception as error:
        logger.error("worker_stopped: error=%s", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
