from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import host_health


class HostHealthTests(unittest.TestCase):
    @patch("host_health.subprocess.run")
    def test_docker_commands_have_a_bounded_timeout(self, run: Any) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        host_health._run(["docker", "compose", "ps"])

        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_runbook_signals_are_aggregated_without_opaque_ids(self) -> None:
        snapshot = {
            "lanes": {"application": {"dead": 1}},
            "dead_job_ids": ["private-job-id"],
            "expired_lease_job_ids": [],
            "oldest_due_pending_seconds": 301,
            "contexts": {
                "delivery": {
                    "outcomes": {"unknown": 2},
                    "unknown_attempt_ids": ["private-attempt-id"],
                    "started_attempts": [],
                },
                "provider": {
                    "states": {"unknown": 1},
                    "unknown_call_ids": ["private-call-id"],
                    "started_call_ids": [],
                    "reported_tokens": 10,
                    "reported_cost_usd": "0.1",
                },
                "speech": {"states": {"failed": 1}, "failed_job_ids": ["private-speech-id"]},
                "billing": {
                    "reservation_states": {},
                    "overdue_reservation_ids": [],
                    "invoice_states": {},
                    "cancellation_states": {},
                    "uncertain_order_ids": [],
                    "refund_states": {},
                    "uncertain_refund_order_ids": [],
                    "receipt_count": 0,
                    "receipt_total_stars": 0,
                },
                "retention": {
                    "request_state_counts": {},
                    "pending_request_ids": [],
                    "oldest_pending_seconds": None,
                    "unexported_tombstone_count": 0,
                    "restore_status": "unavailable",
                },
            },
        }
        summary, signals = host_health.operations_summary(snapshot)

        self.assertEqual(summary["delivery"]["unknown_attempt_count"], 2)
        self.assertEqual(summary["provider"]["unknown_call_count"], 1)
        self.assertEqual(summary["speech"]["failed_request_count"], 1)
        self.assertIn("dead jobs in sample require runbook investigation (1)", signals)
        self.assertIn("unknown provider calls require runbook investigation (1)", signals)
        self.assertNotIn("private-job-id", json.dumps(summary))
        self.assertNotIn("private-call-id", json.dumps(summary))

    def test_restart_increase_is_reported_once_and_unchanged_count_is_clear(self) -> None:
        previous = {"gateway": 1, "speech-worker": 0}
        current = {"gateway": 2, "speech-worker": 0}

        self.assertEqual(
            host_health.restart_errors(current, previous),
            ["container restart observed: gateway (2)"],
        )
        self.assertEqual(host_health.restart_errors(current, current), [])

    def test_configured_disk_memory_and_pending_age_warnings(self) -> None:
        summary = {"oldest_due_pending_seconds": 301}
        resources = {
            "disk_total_bytes": 10_000_000_000,
            "disk_free_bytes": 100_000_000,
            "memory_available_bytes": 100_000_000,
        }

        signals = host_health.threshold_signals(summary, resources)

        self.assertEqual(len(signals), 3)
        self.assertTrue(any("disk" in signal for signal in signals))
        self.assertTrue(any("memory" in signal for signal in signals))
        self.assertTrue(any("pending" in signal for signal in signals))

    def test_configured_thresholds_do_not_warn_on_healthy_values(self) -> None:
        summary = {"oldest_due_pending_seconds": 300}
        resources = {
            "disk_total_bytes": 10_000_000_000,
            "disk_free_bytes": 2_000_000_000,
            "memory_available_bytes": 268_435_456,
        }

        self.assertEqual(host_health.threshold_signals(summary, resources), [])

    def test_meminfo_projection_reports_available_bytes(self) -> None:
        meminfo = "MemTotal:       1000 kB\nMemAvailable:    250 kB\n"
        with patch.object(shutil, "disk_usage", return_value=type(
            "Usage", (), {"total": 100, "free": 25}
        )()), patch.object(Path, "read_text", return_value=meminfo):
            resources = host_health.host_resources()

        self.assertEqual(resources["disk_total_bytes"], 100)
        self.assertEqual(resources["disk_free_bytes"], 25)
        self.assertEqual(resources["memory_total_bytes"], 1_024_000)
        self.assertEqual(resources["memory_available_bytes"], 256_000)
        self.assertIsNone(resources["memory_error"])

    def test_compose_json_array_and_json_lines_are_supported(self) -> None:
        one = {"Service": "gateway", "State": "running"}
        self.assertEqual(host_health._json_records(json.dumps([one])), [one])
        self.assertEqual(host_health._json_records(json.dumps(one) + "\n"), [one])

    def test_state_file_is_private_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state" / "restarts.json"
            host_health._write_state(state, {"gateway": 2})

            self.assertEqual(host_health._read_state(state), {"gateway": 2})
            self.assertEqual(state.read_text(encoding="utf-8"), '{"gateway": 2}\n')

    @patch("host_health.host_resources")
    @patch("host_health._run")
    def test_collect_filters_private_snapshot_fields_and_reports_health(
        self, run: Any, resources: Any
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pin"
            release = root / "releases" / ("a" * 40)
            release.mkdir(parents=True)
            (root / "current").mkdir()
            state = Path(directory) / "state.json"

            snapshot = {
                "generated_at": "2026-10-17T10:00:00Z",
                "database_ready": True,
                "workers": [
                    {"role": role, "status": "running", "worker_id": "private-id"}
                    for role in ("application", "delivery", "gateway", "speech")
                ],
                "private_context": {"prompt": "private prompt"},
            }
            containers = [
                {"ID": f"id-{service}", "Service": service, "State": "running"}
                for service in (
                    "app-worker",
                    "delivery-worker",
                    "gateway",
                    "postgres",
                    "retention",
                    "speech-worker",
                )
            ]
            run.side_effect = [
                subprocess.CompletedProcess([], 0, json.dumps(snapshot), ""),
                subprocess.CompletedProcess([], 0, json.dumps(containers), ""),
                subprocess.CompletedProcess(
                    [], 0, "0\n" * len(containers), ""
                ),
            ]
            resources.return_value = {
                "disk_total_bytes": 8_000_000_000,
                "disk_free_bytes": 4_000_000_000,
                "disk_error": None,
                "memory_total_bytes": 2_000_000_000,
                "memory_available_bytes": 1_000_000_000,
                "memory_error": None,
            }

            with (
                patch.object(host_health, "_ROOT", root),
                patch.object(Path, "resolve", return_value=release),
            ):
                record, exit_code = host_health.collect(state)

            self.assertEqual(exit_code, 0, record)
            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["host"]["disk_free_bytes"], 4_000_000_000)
            self.assertEqual(len(record["workers"]), 4)
            serialized = json.dumps(record)
            self.assertNotIn("private-id", serialized)
            self.assertNotIn("private prompt", serialized)


if __name__ == "__main__":
    unittest.main()
