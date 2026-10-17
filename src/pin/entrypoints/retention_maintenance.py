"""Run local deletion, retention, or restore-replay maintenance once."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pin.adapters.retention.maintenance import replay_before_workers, run_retention_once
from pin.bootstrap.config import SettingsError, load_database_url
from pin.persistence import create_engine, create_session_factory, run_local_async
from pin.platform.recovery import RecoveryFenced, require_runtime_recovery_ready


async def _run(database_url: str, *, artifact: Path, replay: bool) -> dict[str, int]:
    engine = create_engine(database_url)
    sessions = create_session_factory(engine)
    try:
        now = datetime.now(UTC)
        if replay:
            return {
                "replayed_tombstones": await replay_before_workers(
                    sessions, artifact=artifact, now=now
                )
            }
        result = await run_retention_once(sessions, artifact=artifact, now=now)
        return asdict(result)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pin-retention",
        description="Run local retention maintenance or restore deletion fences before workers.",
    )
    parser.add_argument("operation", choices=("run", "replay"))
    parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="path to the independently retained local deletion ledger",
    )
    args = parser.parse_args()
    if not args.artifact.is_absolute():
        parser.error("--artifact must be an absolute path")
    try:
        database_url = load_database_url().get_secret_value()
    except SettingsError as error:
        print(json.dumps({"database_ready": False, "database_error": str(error)}))
        raise SystemExit(2) from None
    if args.operation == "run":
        try:
            require_runtime_recovery_ready(database_url)
        except RecoveryFenced:
            print(json.dumps({"completed": False, "error": "RecoveryFenced"}, sort_keys=True))
            raise SystemExit(2) from None
    try:
        result = run_local_async(
            lambda: _run(
                database_url,
                artifact=args.artifact,
                replay=args.operation == "replay",
            )
        )
    except Exception as error:
        # Keep command output free of database URLs, owner IDs, and payloads.
        print(json.dumps({"completed": False, "error": type(error).__name__}, sort_keys=True))
        raise SystemExit(1) from None
    print(json.dumps({"completed": True, **result}, sort_keys=True))


if __name__ == "__main__":
    main()
