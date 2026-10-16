from __future__ import annotations

import unittest

from startup_readiness import readiness_errors


class StartupReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "database_ready": True,
            "workers": [
                {"role": role, "status": "running"}
                for role in ("application", "delivery", "gateway", "speech")
            ],
        }
        self.containers = [
            {"Service": service, "State": "running"}
            for service in (
                "app-worker",
                "delivery-worker",
                "gateway",
                "postgres",
                "retention",
                "speech-worker",
            )
        ]

    def test_all_services_and_worker_heartbeats_ready(self) -> None:
        self.assertEqual(readiness_errors(self.snapshot, self.containers), [])

    def test_database_only_snapshot_does_not_pass_startup(self) -> None:
        self.snapshot["workers"] = []
        errors = readiness_errors(self.snapshot, self.containers)
        self.assertTrue(any("worker heartbeats not ready" in error for error in errors))

    def test_stale_or_missing_worker_role_fails_startup(self) -> None:
        self.snapshot["workers"][-1]["status"] = "stale"
        errors = readiness_errors(self.snapshot, self.containers)
        self.assertTrue(any("speech" in error for error in errors))

    def test_restarting_container_and_missing_service_fail_startup(self) -> None:
        self.containers[0]["State"] = "restarting"
        self.containers.pop()
        errors = readiness_errors(self.snapshot, self.containers)
        self.assertTrue(any("container is restarting" in error for error in errors))
        self.assertTrue(any("speech-worker" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
