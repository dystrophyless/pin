#!/usr/bin/env python3
"""Emit a redacted host health record and fail on observed runtime faults."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from startup_readiness import readiness_errors

_ROOT = Path("/opt/pin")
_ENV_FILE = _ROOT / "shared" / "runtime.env"
DISK_WARN_FREE_BYTES = 1_073_741_824
DISK_WARN_FREE_RATIO = 0.10
MEMORY_WARN_AVAILABLE_BYTES = 268_435_456
PENDING_WARN_SECONDS = 300


def host_resources() -> dict[str, int | None | str]:
    memory_total: int | None
    memory_available: int | None
    memory_error: str | None
    try:
        disk = shutil.disk_usage("/")
        disk_total: int | None = disk.total
        disk_free: int | None = disk.free
        disk_error: str | None = None
    except OSError as error:
        disk_total = disk_free = None
        disk_error = type(error).__name__

    try:
        memory_values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, value = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable"}:
                memory_values[name] = int(value.strip().split()[0]) * 1024
        memory_total = memory_values["MemTotal"]
        memory_available = memory_values["MemAvailable"]
        memory_error = None
    except (OSError, KeyError, ValueError, IndexError) as error:
        memory_total = memory_available = None
        memory_error = type(error).__name__

    return {
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
        "disk_error": disk_error,
        "memory_total_bytes": memory_total,
        "memory_available_bytes": memory_available,
        "memory_error": memory_error,
    }


def _json_records(output: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = [json.loads(line) for line in output.splitlines() if line.strip()]
    records = parsed if isinstance(parsed, list) else [parsed]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("invalid Compose record")
    return records


def restart_errors(current: dict[str, int], previous: dict[str, int]) -> list[str]:
    return [
        f"container restart observed: {service} ({count})"
        for service, count in sorted(current.items())
        if count > previous.get(service, 0)
    ]


def _counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: count
        for key, count in value.items()
        if isinstance(key, str) and isinstance(count, int) and count >= 0
    }


def _sample_count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def operations_summary(snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    contexts = _mapping(snapshot.get("contexts"))
    delivery = _mapping(contexts.get("delivery"))
    provider = _mapping(contexts.get("provider"))
    speech = _mapping(contexts.get("speech"))
    billing = _mapping(contexts.get("billing"))
    retention = _mapping(contexts.get("retention"))

    lanes = snapshot.get("lanes")
    lane_counts: dict[str, dict[str, int]] = {}
    if isinstance(lanes, dict):
        lane_counts = {
            lane: _counts(counts)
            for lane, counts in lanes.items()
            if isinstance(lane, str)
        }
    provider_states = _counts(provider.get("states"))
    delivery_outcomes = _counts(delivery.get("outcomes"))
    speech_states = _counts(speech.get("states"))
    billing_reservations = _counts(billing.get("reservation_states"))
    billing_invoices = _counts(billing.get("invoice_states"))
    billing_cancellations = _counts(billing.get("cancellation_states"))
    billing_refunds = _counts(billing.get("refund_states"))
    retention_states = _counts(retention.get("request_state_counts"))

    summary: dict[str, Any] = {
        "lanes": lane_counts,
        "oldest_due_pending_seconds": snapshot.get("oldest_due_pending_seconds"),
        "dead_job_sample_count": _sample_count(snapshot.get("dead_job_ids")),
        "expired_lease_sample_count": _sample_count(snapshot.get("expired_lease_job_ids")),
        "heartbeat_stale_after_seconds": snapshot.get("heartbeat_stale_after_seconds"),
        "delivery": {
            "outcomes": delivery_outcomes,
            "unknown_attempt_count": delivery_outcomes.get("unknown", 0),
            "started_attempt_sample_count": _sample_count(delivery.get("started_attempts")),
        },
        "provider": {
            "states": provider_states,
            "unknown_call_count": provider_states.get("unknown", 0),
            "started_call_count": provider_states.get("started", 0),
            "reported_tokens": provider.get("reported_tokens"),
            "reported_cost_usd": provider.get("reported_cost_usd"),
        },
        "speech": {"states": speech_states, "failed_request_count": speech_states.get("failed", 0)},
        "billing": {
            "reservation_states": billing_reservations,
            "overdue_reservation_sample_count": _sample_count(
                billing.get("overdue_reservation_ids")
            ),
            "invoice_states": billing_invoices,
            "cancellation_states": billing_cancellations,
            "uncertain_order_sample_count": _sample_count(billing.get("uncertain_order_ids")),
            "refund_states": billing_refunds,
            "uncertain_refund_sample_count": _sample_count(
                billing.get("uncertain_refund_order_ids")
            ),
            "receipt_count": billing.get("receipt_count"),
            "receipt_total_stars": billing.get("receipt_total_stars"),
        },
        "retention": {
            "request_state_counts": retention_states,
            "pending_request_sample_count": _sample_count(retention.get("pending_request_ids")),
            "oldest_pending_seconds": retention.get("oldest_pending_seconds"),
            "unexported_tombstone_count": retention.get("unexported_tombstone_count"),
            "restore_status": retention.get("restore_status"),
        },
    }
    signals: list[str] = []
    for key, label in (
        ("dead_job_sample_count", "dead jobs in sample"),
        ("expired_lease_sample_count", "expired job leases in sample"),
    ):
        if summary[key]:
            signals.append(f"{label} require runbook investigation ({summary[key]})")
    for value, label in (
        (summary["delivery"]["unknown_attempt_count"], "unknown delivery outcomes"),
        (summary["provider"]["unknown_call_count"], "unknown provider calls"),
        (summary["speech"]["failed_request_count"], "failed speech requests"),
        (summary["billing"]["overdue_reservation_sample_count"], "overdue reservations"),
        (summary["billing"]["uncertain_order_sample_count"], "uncertain payment orders"),
        (summary["billing"]["uncertain_refund_sample_count"], "uncertain refunds"),
        (summary["retention"]["pending_request_sample_count"], "pending deletion exports"),
        (summary["retention"]["unexported_tombstone_count"], "unexported deletion tombstones"),
    ):
        if value:
            signals.append(f"{label} require runbook investigation ({value})")
    return summary, signals


def threshold_signals(
    summary: dict[str, Any], resources: Mapping[str, int | None | str]
) -> list[str]:
    signals: list[str] = []
    disk_total = resources.get("disk_total_bytes")
    disk_free = resources.get("disk_free_bytes")
    if isinstance(disk_total, int) and isinstance(disk_free, int) and disk_total > 0:
        if disk_free < DISK_WARN_FREE_BYTES or disk_free / disk_total < DISK_WARN_FREE_RATIO:
            signals.append("host disk free space is below the configured warning level")
    memory_available = resources.get("memory_available_bytes")
    if isinstance(memory_available, int) and memory_available < MEMORY_WARN_AVAILABLE_BYTES:
        signals.append("host available memory is below the configured warning level")
    pending_age = summary.get("oldest_due_pending_seconds")
    if isinstance(pending_age, (int, float)) and pending_age > PENDING_WARN_SECONDS:
        signals.append("oldest due pending work exceeds the configured warning age")
    return signals


def _compose(release: Path, *arguments: str) -> list[str]:
    return [
        "env",
        f"PIN_IMAGE=pin:{release.name}",
        "docker",
        "compose",
        "--env-file",
        str(_ENV_FILE),
        "--file",
        str(release / "compose.yaml"),
        *arguments,
    ]


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def _read_state(path: Path) -> dict[str, int]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(count, int) or count < 0
        for key, count in value.items()
    ):
        return {}
    return value


def _write_state(path: Path, value: dict[str, int]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def collect(state_file: Path) -> tuple[dict[str, Any], int]:
    try:
        release = (_ROOT / "current").resolve(strict=True)
        if release.parent != _ROOT / "releases":
            raise ValueError("current release is outside the release directory")
        image_id = release.name
        if not image_id or any(char not in "0123456789abcdef" for char in image_id):
            raise ValueError("current release id is invalid")

        operations_result = _run(
            _compose(
                release,
                "--profile",
                "ops",
                "run",
                "--rm",
                "--pull",
                "never",
                "--no-deps",
                "operations",
            ),
            cwd=release,
        )
        snapshot = json.loads(operations_result.stdout)
        if not isinstance(snapshot, dict):
            raise ValueError("invalid operations snapshot")

        containers_result = _run(
            _compose(release, "--profile", "ops", "ps", "--all", "--format", "json"),
            cwd=release,
        )
        if containers_result.returncode != 0:
            raise RuntimeError("Compose status unavailable")
        containers = _json_records(containers_result.stdout)

        identified_containers: list[tuple[str, str]] = []
        for container in containers:
            service = container.get("Service")
            container_id = container.get("ID") or container.get("Id")
            if isinstance(service, str) and isinstance(container_id, str):
                identified_containers.append((service, container_id))
        inspect_result = _run(
            ["docker", "inspect", "--format={{.RestartCount}}"]
            + [container_id for _, container_id in identified_containers]
        )
        if inspect_result.returncode != 0:
            raise RuntimeError("container restart status unavailable")
        restart_values = [int(value) for value in inspect_result.stdout.splitlines()]
        if len(restart_values) != len(identified_containers):
            raise RuntimeError("container restart status incomplete")
        restarts = {
            service: count
            for (service, _), count in zip(identified_containers, restart_values, strict=True)
        }

        resources = host_resources()
        errors = readiness_errors(snapshot, containers)
        operational_summary, operational_signals = operations_summary(snapshot)
        errors.extend(operational_signals)
        errors.extend(threshold_signals(operational_summary, resources))
        if resources["disk_error"] is not None:
            errors.append(f"host disk measurement unavailable ({resources['disk_error']})")
        if resources["memory_error"] is not None:
            errors.append(f"host memory measurement unavailable ({resources['memory_error']})")
        errors.extend(restart_errors(restarts, _read_state(state_file)))
        if operations_result.returncode != 0 and not errors:
            errors.append("operations snapshot command failed")
        _write_state(state_file, restarts)

        workers = snapshot.get("workers")
        worker_status = [
            {"role": item["role"], "status": item["status"]}
            for item in (workers if isinstance(workers, list) else [])
            if isinstance(item, dict)
            and isinstance(item.get("role"), str)
            and isinstance(item.get("status"), str)
        ]
        record = {
            "generated_at": snapshot.get("generated_at"),
            "status": "failed" if errors or operations_result.returncode else "ok",
            "database_ready": snapshot.get("database_ready") is True,
            "workers": worker_status,
            "operations": operational_summary,
            "containers": [
                {
                    "service": item.get("Service"),
                    "state": item.get("State"),
                    "restart_count": restarts.get(str(item.get("Service"))),
                }
                for item in containers
                if isinstance(item.get("Service"), str)
            ],
            "host": resources,
            "errors": errors,
        }
        return record, 1 if record["status"] == "failed" else 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
        TypeError,
    ) as error:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "failed",
            "errors": [f"host health check incomplete ({type(error).__name__})"],
        }, 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("/var/lib/pin-host-health/restarts.json"),
    )
    args = parser.parse_args(argv[1:])
    record, exit_code = collect(args.state_file)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
