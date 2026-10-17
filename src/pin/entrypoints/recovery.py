"""Operator CLI for the restored-database recovery fence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text

from pin.bootstrap.config import SettingsError, load_database_url
from pin.platform.recovery import _arm, _release, recovery_is_ready


def main() -> None:
    parser = argparse.ArgumentParser(prog="pin-recovery")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("check")
    subparsers.add_parser("status")
    arm = subparsers.add_parser("arm")
    arm.add_argument("--snapshot-id", required=True)
    arm.add_argument("--ledger-id", required=True)
    release = subparsers.add_parser("release")
    for name in ("deletion", "delivery", "payment", "usage"):
        release.add_argument(f"--{name}-evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        database_url = load_database_url().get_secret_value()
    except SettingsError as error:
        print(json.dumps({"database_ready": False, "database_error": str(error)}))
        raise SystemExit(2) from None
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        if args.operation == "arm":
            _arm(engine, args.snapshot_id, args.ledger_id)
            print(
                json.dumps(
                    {
                        "state": "fenced",
                        "snapshot_id": args.snapshot_id,
                        "ledger_id": args.ledger_id,
                    },
                    sort_keys=True,
                )
            )
        elif args.operation == "release":
            _release(
                engine,
                args.deletion_evidence,
                args.delivery_evidence,
                args.payment_evidence,
                args.usage_evidence,
            )
            print(json.dumps({"state": "ready", "evidence_receipts_recorded": True}))
        else:
            with engine.connect() as connection:
                row = (
                    connection.execute(
                        text("""
                            SELECT state, snapshot_id, ledger_id, snapshot_at, fenced_at,
                                   released_at, deletion_evidence_sha256,
                                   delivery_evidence_sha256, payment_evidence_sha256,
                                   usage_evidence_sha256,
                                   current_database() AS database_name
                            FROM platform.recovery_gate WHERE singleton = true
                        """)
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                raise RuntimeError("recovery gate row is missing")
            print(json.dumps(dict(row), default=str, sort_keys=True))
            if args.operation == "check" and not recovery_is_ready(
                row["state"],
                snapshot_id=row["snapshot_id"],
                released_at=row["released_at"],
                database_name=row["database_name"],
            ):
                raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as error:
        print(json.dumps({"completed": False, "error": type(error).__name__}, sort_keys=True))
        raise SystemExit(2) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
