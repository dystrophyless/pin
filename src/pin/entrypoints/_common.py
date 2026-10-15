"""Shared startup check for the four independently composed process roles."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from pin.bootstrap.composition import build_runtime
from pin.bootstrap.config import ProcessRole, SettingsError
from pin.observability import (
    configure_logging,
    log_speech_dependencies_missing,
    log_startup_configuration_rejection,
)

logger = logging.getLogger("pin.runtime")


def main_for(role: ProcessRole, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"pin-{role.value}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and composition without connecting to external systems",
    )
    args = parser.parse_args(argv)
    configure_logging()
    try:
        build_runtime(role)
    except SettingsError as error:
        log_startup_configuration_rejection(
            logger, error.diagnostic_category, error.diagnostic_fields
        )
        return 2
    if role is ProcessRole.SPEECH_WORKER:
        try:
            from pin.bootstrap.speech import require_speech_runtime

            require_speech_runtime()
        except RuntimeError:
            log_speech_dependencies_missing(logger)
            return 2
    logger.info("startup_ready: role=%s", role.value)
    if not args.check:
        logger.error("runtime adapters are not implemented for role=%s", role.value)
        return 3
    return 0


def run(role: ProcessRole) -> None:
    raise SystemExit(main_for(role, sys.argv[1:]))
