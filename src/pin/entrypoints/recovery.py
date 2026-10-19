"""Operator CLI for the restored-database recovery fence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pin.bootstrap.config import SettingsError, load_database_url
from pin.contexts.identity.api import list_granted_processing_consent_owners
from pin.contexts.identity.application.processing_consent import (
    invalidate_restored_processing_consent,
)
from pin.contexts.identity.infrastructure.store import SqlAlchemyIdentityStore
from pin.persistence import run_local_async
from pin.platform.recovery import (
    _arm,
    _record_consent_invalidation,
    _release,
    recovery_is_ready,
)


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
            with engine.connect() as connection:
                generation = connection.execute(
                    text("""
                        SELECT state, snapshot_id, ledger_id
                        FROM platform.recovery_gate WHERE singleton = true
                    """)
                ).mappings().one_or_none()
            if generation is None:
                raise RuntimeError("recovery gate row is missing")
            if generation["state"] == "fenced":
                run_local_async(
                    lambda: _invalidate_restored_consents(
                        database_url,
                        engine,
                        snapshot_id=generation["snapshot_id"],
                        ledger_id=generation["ledger_id"],
                    )
                )
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
                                   consent_invalidated_snapshot_id,
                                   consent_invalidated_ledger_id,
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
                ledger_id=row["ledger_id"],
                released_at=row["released_at"],
                database_name=row["database_name"],
                consent_invalidated_snapshot_id=row["consent_invalidated_snapshot_id"],
                consent_invalidated_ledger_id=row["consent_invalidated_ledger_id"],
            ):
                raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as error:
        print(json.dumps({"completed": False, "error": type(error).__name__}, sort_keys=True))
        raise SystemExit(2) from None
    finally:
        engine.dispose()


async def _invalidate_restored_consents(
    database_url: str,
    engine: Engine,
    *,
    snapshot_id: str,
    ledger_id: str,
) -> None:
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}:
        url = url.set(drivername="postgresql+psycopg")
    async_engine = create_async_engine(url, pool_pre_ping=True)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        async_engine, expire_on_commit=False
    )
    try:
        async with sessions() as session:
            owners = await list_granted_processing_consent_owners(
                SqlAlchemyIdentityStore(session)
            )
        for owner_id in owners:
            async with sessions() as session, session.begin():
                await invalidate_restored_processing_consent(
                    SqlAlchemyIdentityStore(session), owner_id=owner_id
                )
        _record_consent_invalidation(engine, snapshot_id, ledger_id)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    main()
