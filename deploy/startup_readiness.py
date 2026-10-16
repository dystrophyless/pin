"""Fail closed until Compose services and worker heartbeats are ready."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_ROLES = {"application", "delivery", "gateway", "speech"}
REQUIRED_SERVICES = {
    "app-worker",
    "delivery-worker",
    "gateway",
    "postgres",
    "retention",
    "speech-worker",
}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _read_compose_json_lines(path: Path) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                container = json.loads(line)
                if not isinstance(container, dict):
                    raise ValueError("invalid container record")
                containers.append(container)
    return containers


def readiness_errors(
    snapshot: Any, containers: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(snapshot, dict):
        return ["database operations snapshot has an invalid shape"]
    if snapshot.get("database_ready") is not True:
        errors.append("database operations snapshot is not ready")

    workers = snapshot.get("workers")
    if not isinstance(workers, list):
        workers = []
    running_roles = {
        role
        for worker in workers
        if isinstance(worker, dict)
        and worker.get("status") == "running"
        and isinstance((role := worker.get("role")), str)
    }
    missing_roles = sorted(REQUIRED_ROLES - running_roles)
    if missing_roles:
        errors.append(f"worker heartbeats not ready: {', '.join(missing_roles)}")

    services: dict[str, set[str]] = {}
    restarting = False
    for container in containers:
        service = container.get("Service")
        state = container.get("State")
        if isinstance(service, str) and isinstance(state, str):
            services.setdefault(service, set()).add(state.lower())
            restarting = restarting or state.lower() == "restarting"
    if restarting:
        errors.append("a Compose container is restarting")

    missing_services = sorted(
        service
        for service in REQUIRED_SERVICES
        if "running" not in services.get(service, set())
    )
    if missing_services:
        errors.append(f"Compose services not running: {', '.join(missing_services)}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: startup_readiness.py OPERATIONS.json COMPOSE.jsonl", file=sys.stderr)
        return 2
    try:
        snapshot = _read_json(Path(argv[1]))
        containers = _read_compose_json_lines(Path(argv[2]))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"could not read startup status ({type(error).__name__})", file=sys.stderr)
        return 2
    errors = readiness_errors(snapshot, containers)
    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    print("runtime workers ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
