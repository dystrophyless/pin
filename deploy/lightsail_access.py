"""Prepare temporary Lightsail SSH credentials without printing secrets."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return parsed.astimezone(UTC)
    raise ValueError("timestamp must be an epoch value or an ISO timestamp")


def prepare_access(
    payload: dict[str, Any], now: datetime
) -> tuple[str, str, str, str, str]:
    details = payload.get("accessDetails")
    if not isinstance(details, dict):
        raise ValueError("missing Lightsail access details")
    expiry = _timestamp(details.get("expiresAt"))
    current_time = now.astimezone(UTC)
    if expiry <= current_time:
        raise ValueError("temporary SSH credentials are expired")
    if (expiry - current_time).total_seconds() <= 30:
        raise ValueError("temporary SSH credentials are too close to expiry")

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
