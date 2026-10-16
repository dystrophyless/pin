"""Prepare temporary Lightsail SSH credentials without printing secrets."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CERTIFICATE_VALIDITY = re.compile(
    r"^\s*Valid: from (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}) "
    r"to (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s*$",
    re.MULTILINE,
)


def _certificate_validity(certificate: str) -> tuple[datetime, datetime]:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix="-cert.pub", delete=False
    ) as stream:
        certificate_path = Path(stream.name)
        stream.write(certificate.rstrip() + "\n")

    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "TZ": "UTC"})
    try:
        result = subprocess.run(
            ["ssh-keygen", "-L", "-f", str(certificate_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("could not inspect temporary SSH certificate") from error
    finally:
        certificate_path.unlink(missing_ok=True)

    matches = _CERTIFICATE_VALIDITY.findall(result.stdout)
    if len(matches) != 1:
        raise ValueError("temporary SSH certificate has no valid UTC interval")
    starts_at, expires_at = (
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        for value in matches[0]
    )
    return starts_at, expires_at


def prepare_access(
    payload: dict[str, Any], now: datetime
) -> tuple[str, str, str, str, str]:
    details = payload.get("accessDetails")
    if not isinstance(details, dict):
        raise ValueError("missing Lightsail access details")
    current_time = now.astimezone(UTC)

    host = str(ipaddress.IPv4Address(details["ipAddress"]))
    username = details["username"]
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", username):
        raise ValueError("invalid SSH username")

    private_key = details.get("privateKey")
    certificate = details.get("certKey")
    if not isinstance(private_key, str) or not private_key.strip():
        raise ValueError("missing temporary SSH private key")
    if not isinstance(certificate, str) or not certificate.startswith("ssh-"):
        raise ValueError("missing temporary SSH certificate")
    starts_at, expires_at = _certificate_validity(certificate)
    if starts_at > current_time:
        raise ValueError("temporary SSH certificate is not yet valid")
    if expires_at <= current_time:
        raise ValueError("temporary SSH certificate is expired")
    if (expires_at - current_time).total_seconds() <= 30:
        raise ValueError("temporary SSH certificate is too close to expiry")

    host_keys = details.get("hostKeys")
    if not isinstance(host_keys, list):
        raise ValueError("missing Lightsail SSH host keys")
    known_hosts: list[str] = []
    for host_key in host_keys:
        if not isinstance(host_key, dict):
            continue
        algorithm = host_key.get("algorithm")
        public_key = host_key.get("publicKey")
        if not isinstance(algorithm, str) or not (
            algorithm.startswith("ssh-") or algorithm.startswith("ecdsa-sha2-")
        ):
            continue
        if not isinstance(public_key, str):
            raise ValueError("invalid SSH host key")
        encoded_key = (
            public_key[len(algorithm) + 1 :]
            if public_key.startswith(algorithm + " ")
            else public_key
        )
        if not encoded_key or any(ch.isspace() for ch in encoded_key):
            raise ValueError("empty SSH host key")
        known_hosts.append(f"{host} {algorithm} {encoded_key}")
    if not known_hosts:
        raise ValueError("Lightsail returned no SSH host keys")

    return host, username, private_key, certificate, "\n".join(known_hosts) + "\n"


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path)
    parser.add_argument("private_key", type=Path)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("known_hosts", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.response.read_text(encoding="utf-8"))
    host, username, private_key, certificate, known_hosts = prepare_access(
        payload, datetime.now(UTC)
    )
    _write_secret(args.private_key, private_key)
    _write_secret(args.certificate, certificate)
    _write_secret(args.known_hosts, known_hosts)
    print(f"{host}\t{username}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid Lightsail access response: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
