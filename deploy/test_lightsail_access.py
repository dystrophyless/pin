from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

from lightsail_access import prepare_access


class LightsailAccessTests(unittest.TestCase):
    def _response(self) -> dict[str, Any]:
        return {
            "accessDetails": {
                "protocol": "ssh",
                "instanceName": "pin-production",
                "ipAddress": "203.0.113.7",
                "ipv6Addresses": [],
                "username": "ubuntu",
                "privateKey": "private key contents",
                "certKey": "ssh-rsa-cert-v01@openssh.com AAAAB3NzaC1yc2E temporary-cert",
                "hostKeys": [
                    {
                        "algorithm": "ssh-ed25519",
                        "publicKey": "AAAAC3NzaC1lZDI1NTE5AAAA",
                        "witnessedAt": "2026-10-17T00:00:00Z",
                        "fingerprintSHA1": "sha1-fingerprint",
                        "fingerprintSHA256": "sha256-fingerprint",
                    },
                    {
                        "algorithm": "ssh-rsa",
                        "publicKey": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ",
                        "witnessedAt": "2026-10-17T00:00:00Z",
                        "fingerprintSHA1": "sha1-fingerprint",
                        "fingerprintSHA256": "sha256-fingerprint",
                    },
                    {"algorithm": "x509-cert", "publicKey": "not an SSH key"},
                ],
            }
        }

    @patch("lightsail_access.subprocess.run")
    def test_accepts_observed_response_without_optional_expires_at(
        self, run: Any
    ) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        completed = CompletedProcess(
            args=["ssh-keygen", "-L"],
            returncode=0,
            stdout=(
                "test-cert:\n\tType: RSA-CERT\n"
                "\tValid: from 2026-10-16T23:27:21 to 2026-10-17T00:13:00\n"
            ),
            stderr="",
        )
        response = self._response()
        self.assertNotIn("expiresAt", response["accessDetails"])

        def inspect_certificate(args: list[str], **kwargs: Any) -> CompletedProcess[str]:
            self.assertEqual(args[:2], ["ssh-keygen", "-L"])
            self.assertEqual(Path(args[-1]).read_text(encoding="utf-8").strip(), response[
                "accessDetails"
            ]["certKey"])
            self.assertEqual(kwargs["env"]["TZ"], "UTC")
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            return completed

        run.side_effect = inspect_certificate
        result = prepare_access(response, now)

        self.assertEqual(result[0:2], ("203.0.113.7", "ubuntu"))
        self.assertIn("203.0.113.7 ssh-ed25519 AAAAC3", result[4])
        self.assertIn("203.0.113.7 ssh-rsa AAAAB3", result[4])
        self.assertNotIn("x509-cert", result[4])

    @patch("lightsail_access.subprocess.run")
    def test_rejects_certificate_expiring_within_thirty_seconds(self, run: Any) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        run.return_value = CompletedProcess(
            args=["ssh-keygen", "-L"],
            returncode=0,
            stdout="Valid: from 2026-10-16T23:00:00 to 2026-10-17T00:00:30\n",
            stderr="",
        )
        with self.assertRaisesRegex(ValueError, "too close to expiry"):
            prepare_access(self._response(), now)

    @patch("lightsail_access.subprocess.run")
    def test_rejects_expired_certificate(self, run: Any) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        run.return_value = CompletedProcess(
            args=["ssh-keygen", "-L"],
            returncode=0,
            stdout="Valid: from 2026-10-16T23:00:00 to 2026-10-16T23:59:59\n",
            stderr="",
        )
        with self.assertRaisesRegex(ValueError, "certificate is expired"):
            prepare_access(self._response(), now)

    @patch("lightsail_access.subprocess.run")
    def test_rejects_certificate_not_yet_valid(self, run: Any) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        run.return_value = CompletedProcess(
            args=["ssh-keygen", "-L"],
            returncode=0,
            stdout="Valid: from 2026-10-17T00:00:01 to 2026-10-17T00:10:00\n",
            stderr="",
        )
        with self.assertRaisesRegex(ValueError, "not yet valid"):
            prepare_access(self._response(), now)

    @patch("lightsail_access.subprocess.run")
    def test_rejects_missing_certificate_validity_output(self, run: Any) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        run.return_value = CompletedProcess(
            args=["ssh-keygen", "-L"], returncode=0, stdout="Type: RSA-CERT\n", stderr=""
        )
        with self.assertRaisesRegex(ValueError, "no valid UTC interval"):
            prepare_access(self._response(), now)

    @patch("lightsail_access.subprocess.run")
    def test_requires_at_least_one_ssh_host_key(self, run: Any) -> None:
        run.return_value = CompletedProcess(
            args=["ssh-keygen", "-L"],
            returncode=0,
            stdout="Valid: from 2026-10-16T23:00:00 to 2026-10-17T00:10:00\n",
            stderr="",
        )
        response = self._response()
        response["accessDetails"]["hostKeys"] = [
            {"algorithm": "x509-cert", "publicKey": "certificate"}
        ]
        with self.assertRaisesRegex(ValueError, "no SSH host keys"):
            prepare_access(response, datetime(2026, 10, 17, 0, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
