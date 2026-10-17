"""Fail-closed process startup gate for restored database snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession


class RecoveryFenced(RuntimeError):
    """The database requires recovery reconciliation before runtime work."""


def recovery_is_ready(
    state: str | None,
    *,
    snapshot_id: str | None,
    released_at: datetime | None,
    database_name: str,
) -> bool:
    if state != "ready":
        return False
    return not database_name.startswith("pin_recovery_") or (
        snapshot_id is not None and released_at is not None
    )


def require_recovery_ready(engine: Engine) -> None:
    """Reject runtime startup when the recovery row is missing or fenced."""
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("""
                    SELECT state, snapshot_id, released_at, current_database()
                    FROM platform.recovery_gate WHERE singleton = true
                """)
            ).one_or_none()
    except Exception as error:
        raise RecoveryFenced("recovery gate unavailable") from error
    if row is None or not recovery_is_ready(
        row.state,
        snapshot_id=row.snapshot_id,
        released_at=row.released_at,
        database_name=row.current_database,
    ):
        raise RecoveryFenced("recovery reconciliation required")


def require_runtime_recovery_ready(database_url: str) -> None:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        require_recovery_ready(engine)
    finally:
        engine.dispose()


async def snapshot_delivery_is_uncertain(
    session: AsyncSession,
    *,
    created_at: datetime,
    scheduled_at: datetime,
    source_created_at: datetime | None = None,
    regenerated_occurrence: bool = False,
) -> bool:
    """Identify deliveries that could have been sent before runtime was fenced."""
    row = (
        await session.execute(
            text("""
            SELECT fenced_at
            FROM platform.recovery_gate
            WHERE singleton = true AND state = 'ready'
              AND snapshot_at IS NOT NULL AND fenced_at IS NOT NULL
              AND released_at IS NOT NULL
        """)
        )
    ).one_or_none()
    if row is None:
        return False
    recovery_at = row[0]
    created = created_at.astimezone(UTC)
    recovery = recovery_at.astimezone(UTC)
    regenerated_from_old_source = (
        source_created_at is not None
        and source_created_at.astimezone(UTC) <= recovery
        and regenerated_occurrence
    )
    return bool(
        (created <= recovery or regenerated_from_old_source)
        and scheduled_at.astimezone(UTC) <= recovery
    )


def _receipt_hash(
    path: Path, *, kind: str, snapshot_id: str, ledger_id: str, fenced_at: datetime
) -> str:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError(f"{kind} evidence must be an existing absolute regular file")
    raw = path.read_bytes()
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("status") != "operator_verified"
        or not isinstance(value.get("checked_at"), str)
        or not isinstance(value.get("sources"), list)
        or not value["sources"]
        or not isinstance(value.get("window"), dict)
    ):
        raise ValueError(f"{kind} evidence receipt has an invalid shape")
    if value.get("snapshot_id") != snapshot_id or value.get("reconciled") is not True:
        raise ValueError(f"{kind} evidence is not reconciled for this snapshot")
    if kind == "deletion" and (
        value.get("ledger_id") != ledger_id or value.get("tombstone_coverage") != "complete"
    ):
        raise ValueError("deletion evidence does not verify complete newer-ledger coverage")
    checked_at = datetime.fromisoformat(value["checked_at"].replace("Z", "+00:00"))
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError(f"{kind} evidence timestamp must include a timezone")
    if checked_at.astimezone(UTC) < fenced_at.astimezone(UTC):
        raise ValueError(f"{kind} evidence predates this recovery fence")
    expected_start = datetime.strptime(snapshot_id, "pin-%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    try:
        window_start = datetime.fromisoformat(value["window"]["start"].replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(value["window"]["end"].replace("Z", "+00:00"))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError(f"{kind} evidence window must include start and end timestamps") from None
    if (
        window_start.tzinfo is None
        or window_end.tzinfo is None
        or window_start.astimezone(UTC) != expected_start
        or window_end.astimezone(UTC) != fenced_at.astimezone(UTC)
    ):
        raise ValueError(f"{kind} evidence window does not match this recovery interval")
    for source in value["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError(f"{kind} evidence sources must reference local evidence files")
        evidence = Path(source["path"])
        if (
            not evidence.is_absolute()
            or evidence.is_symlink()
            or not evidence.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", "")))
            or hashlib.sha256(evidence.read_bytes()).hexdigest() != source["sha256"]
        ):
            raise ValueError(f"{kind} evidence source file is missing or has a different digest")
    return hashlib.sha256(raw).hexdigest()


def _arm(engine: Engine, snapshot_id: str, ledger_id: str) -> None:
    if not re.fullmatch(r"pin-\d{8}T\d{6}Z", snapshot_id) or not re.fullmatch(
        r"pin-\d{8}T\d{6}Z", ledger_id
    ):
        raise ValueError("backup IDs must use the verified pin- timestamp format")
    if ledger_id <= snapshot_id:
        raise ValueError("deletion ledger must come from a newer verified backup")
    snapshot_at = datetime.strptime(snapshot_id, "pin-%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    with engine.begin() as connection:
        changed = connection.execute(
            text("""
                UPDATE platform.recovery_gate
                SET state = 'fenced', snapshot_id = :snapshot_id, snapshot_at = :snapshot_at,
                    ledger_id = :ledger_id, fenced_at = :fenced_at,
                    deletion_evidence_sha256 = NULL,
                    delivery_evidence_sha256 = NULL,
                    payment_evidence_sha256 = NULL,
                    usage_evidence_sha256 = NULL,
                    released_at = NULL
                WHERE singleton = true AND state = 'ready'
                  AND current_database() LIKE 'pin!_recovery!_%' ESCAPE '!'
            """),
            {
                "snapshot_id": snapshot_id,
                "snapshot_at": snapshot_at,
                "ledger_id": ledger_id,
                "fenced_at": datetime.now(UTC),
            },
        ).rowcount
    if changed != 1:
        raise RuntimeError("recovery gate is missing, fenced, or not a recovery target")


def _release(engine: Engine, deletion: Path, delivery: Path, payment: Path, usage: Path) -> None:
    with engine.begin() as connection:
        row = (
            connection.execute(
                text("""
            SELECT state, snapshot_id, ledger_id, fenced_at
            FROM platform.recovery_gate WHERE singleton = true FOR UPDATE
        """)
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["state"] != "fenced" or row["fenced_at"] is None:
            raise RuntimeError("recovery gate is not fenced")
        snapshot_id = row["snapshot_id"]
        ledger_id = row["ledger_id"]
        hashes = {
            "deletion_evidence_sha256": _receipt_hash(
                deletion,
                kind="deletion",
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
                fenced_at=row["fenced_at"],
            ),
            "delivery_evidence_sha256": _receipt_hash(
                delivery,
                kind="delivery",
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
                fenced_at=row["fenced_at"],
            ),
            "payment_evidence_sha256": _receipt_hash(
                payment,
                kind="payment",
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
                fenced_at=row["fenced_at"],
            ),
            "usage_evidence_sha256": _receipt_hash(
                usage,
                kind="usage",
                snapshot_id=snapshot_id,
                ledger_id=ledger_id,
                fenced_at=row["fenced_at"],
            ),
        }
        changed = connection.execute(
            text("""
                UPDATE platform.recovery_gate
                SET state = 'ready', deletion_evidence_sha256 = :deletion_evidence_sha256,
                    delivery_evidence_sha256 = :delivery_evidence_sha256,
                    payment_evidence_sha256 = :payment_evidence_sha256,
                    usage_evidence_sha256 = :usage_evidence_sha256,
                    released_at = :released_at
                WHERE singleton = true AND state = 'fenced'
                  AND snapshot_id IS NOT NULL AND ledger_id IS NOT NULL
                  AND ledger_id > snapshot_id
            """),
            {**hashes, "released_at": datetime.now(UTC)},
        ).rowcount
    if changed != 1:
        raise RuntimeError("recovery gate is not armed with a newer deletion ledger")
