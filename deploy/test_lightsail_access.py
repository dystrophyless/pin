from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from lightsail_access import prepare_access


class LightsailAccessTests(unittest.TestCase):
    def _response(self, expires_at: Any) -> dict[str, Any]:
        return {
            "accessDetails": {
                "expiresAt": expires_at,
                "ipAddress": "203.0.113.7",
                "username": "ubuntu",
                "privateKey": "private key contents",
                "certKey": "ssh-rsa-cert-v01@openssh.com temporary-cert",
                "hostKeys": [
                    {"algorithm": "ssh-ed25519", "publicKey": "AAAAC3NzaC1lZDI1NTE5AAAA"},
                    {
                        "algorithm": "ssh-rsa",
                        "publicKey": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ",
                    },
                    {"algorithm": "x509-cert", "publicKey": "not an SSH key"},
                ],
            }
        }

    def test_accepts_aws_cli_iso_timestamp_and_ssh_keys_without_validity_fields(self) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        result = prepare_access(self._response("2026-10-17T00:05:00Z"), now)

        self.assertEqual(result[0:2], ("203.0.113.7", "ubuntu"))
        self.assertIn("203.0.113.7 ssh-ed25519 AAAAC3", result[4])
        self.assertIn("203.0.113.7 ssh-rsa AAAAB3", result[4])
        self.assertNotIn("x509-cert", result[4])

    def test_accepts_api_epoch_timestamp(self) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        result = prepare_access(self._response(1792195500), now)
        self.assertEqual(result[1], "ubuntu")

    def test_rejects_credentials_expiring_within_thirty_seconds(self) -> None:
        now = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "too close to expiry"):
            prepare_access(self._response("2026-10-17T00:00:30Z"), now)

    def test_requires_at_least_one_ssh_host_key(self) -> None:
        response = self._response("2026-10-17T00:05:00Z")
        response["accessDetails"]["hostKeys"] = [
            {"algorithm": "x509-cert", "publicKey": "certificate"}
        ]
        with self.assertRaisesRegex(ValueError, "no SSH host keys"):
            prepare_access(response, datetime(2026, 10, 17, 0, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
