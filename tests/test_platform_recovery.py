from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pin.platform.recovery import (
    _arm,
    _receipt_hash,
    recovery_is_ready,
    snapshot_delivery_is_uncertain,
)


def _receipt(tmp_path: Path, *, kind: str = "usage", coverage: str | None = None) -> Path:
    evidence = tmp_path / f"{kind}-source.json"
    evidence.write_text('{"records": []}', encoding="utf-8")
    value = {
        "status": "operator_verified",
        "checked_at": "2026-10-17T12:05:00Z",
        "snapshot_id": "pin-20261017T120000Z",
        "window": {
            "start": "2026-10-17T12:00:00Z",
            "end": "2026-10-17T12:01:00Z",
        },
        "reconciled": True,
        "sources": [
            {
                "path": str(evidence.resolve()),
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
        ],
    }
    if kind == "deletion":
        value.update(
            {
                "ledger_id": "pin-20261017T120100Z",
                "tombstone_coverage": coverage or "complete",
            }
        )
    receipt = tmp_path / f"{kind}.json"
    receipt.write_text(json.dumps(value), encoding="utf-8")
    return receipt


def test_arm_rejects_non_timestamp_backup_ids_before_database_access() -> None:
    with pytest.raises(ValueError, match="backup IDs"):
        _arm(None, "pin-current", "pin-20261017T120000Z")  # type: ignore[arg-type]


def test_arm_requires_ledger_bundle_newer_than_database_snapshot() -> None:
    with pytest.raises(ValueError, match="newer"):
        _arm(None, "pin-20261017T120000Z", "pin-20261017T120000Z")  # type: ignore[arg-type]


def test_arm_rearms_a_released_recovery_database_and_clears_old_receipts() -> None:
    class Connection:
        statement = ""
        parameters: dict[str, object] = {}

        def execute(self, statement, parameters=None):
            self.statement = str(statement)
            self.parameters = parameters or {}
            if str(statement).lstrip().startswith("SELECT"):
                class Rows:
                    def mappings(self):
                        return self

                    def one_or_none(self):
                        return {
                            "state": "ready",
                            "snapshot_id": None,
                            "ledger_id": None,
                            "database_name": "pin_recovery_test",
                        }

                return Rows()
            return SimpleNamespace(rowcount=1)

    class Engine:
        connection = Connection()

        def begin(self):
            return self

        def __enter__(self):
            return self.connection

        def __exit__(self, *_args):
            return False

    engine = Engine()
    _arm(engine, "pin-20261018T120000Z", "pin-20261018T120100Z")  # type: ignore[arg-type]

    assert "state = 'ready'" in engine.connection.statement
    assert "current_database() LIKE 'pin!_recovery!_%' ESCAPE '!'" in engine.connection.statement
    assert "snapshot_id IS NULL" not in engine.connection.statement
    for field in (
        "deletion_evidence_sha256",
        "delivery_evidence_sha256",
        "payment_evidence_sha256",
        "usage_evidence_sha256",
        "released_at",
    ):
        assert f"{field} = NULL" in engine.connection.statement


def test_arm_refuses_to_rearm_a_database_that_is_already_fenced() -> None:
    class Connection:
        def execute(self, statement, _parameters=None):
            if str(statement).lstrip().startswith("SELECT"):
                class Rows:
                    def mappings(self):
                        return self

                    def one_or_none(self):
                        return {
                            "state": "fenced",
                            "snapshot_id": "pin-20261017T120000Z",
                            "ledger_id": "pin-20261017T120100Z",
                            "database_name": "pin_recovery_test",
                        }

                return Rows()
            return SimpleNamespace(rowcount=0)

    class Engine:
        def begin(self):
            return self

        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    with pytest.raises(RuntimeError, match="another recovery generation"):
        _arm(Engine(), "pin-20261018T120000Z", "pin-20261018T120100Z")  # type: ignore[arg-type]


def test_restore_target_stays_fenced_until_operator_release() -> None:
    assert not recovery_is_ready(
        "ready",
        snapshot_id=None,
        released_at=None,
        database_name="pin_recovery_restore_a",
    )
    assert not recovery_is_ready(
        "fenced",
        snapshot_id="pin-20261017T120000Z",
        released_at=None,
        database_name="pin_recovery_restore_a",
    )
    assert recovery_is_ready(
        "ready",
        snapshot_id="pin-20261017T120000Z",
        ledger_id="pin-20261017T120100Z",
        released_at=datetime(2026, 10, 17, 12, 5, tzinfo=UTC),
        database_name="pin_recovery_restore_a",
        consent_invalidated_snapshot_id="pin-20261017T120000Z",
        consent_invalidated_ledger_id="pin-20261017T120100Z",
    )
    assert recovery_is_ready(
        "ready",
        snapshot_id=None,
        released_at=None,
        database_name="pin_production",
    )


def test_release_receipt_is_bound_to_snapshot_recovery_and_timestamp(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)

    assert _receipt_hash(
        receipt,
        kind="usage",
        snapshot_id="pin-20261017T120000Z",
        ledger_id="pin-20261017T120100Z",
        fenced_at=datetime(2026, 10, 17, 12, 1, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="predates"):
        _receipt_hash(
            receipt,
            kind="usage",
            snapshot_id="pin-20261017T120000Z",
            ledger_id="pin-20261017T120100Z",
            fenced_at=datetime(2026, 10, 17, 12, 6, tzinfo=UTC),
        )


def test_deletion_receipt_requires_explicit_complete_coverage(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path, kind="deletion", coverage="partial")
    with pytest.raises(ValueError, match="complete newer-ledger coverage"):
        _receipt_hash(
            receipt,
            kind="deletion",
            snapshot_id="pin-20261017T120000Z",
            ledger_id="pin-20261017T120100Z",
            fenced_at=datetime(2026, 10, 17, 12, 1, tzinfo=UTC),
        )


def test_receipt_requires_matching_window_and_evidence_digest(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["window"]["end"] = "2026-10-17T12:02:00Z"
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="window does not match"):
        _receipt_hash(
            receipt,
            kind="usage",
            snapshot_id="pin-20261017T120000Z",
            ledger_id="pin-20261017T120100Z",
            fenced_at=datetime(2026, 10, 17, 12, 1, tzinfo=UTC),
        )
    value["window"]["end"] = "2026-10-17T12:01:00Z"
    value["sources"][0]["sha256"] = "0" * 64
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="source file is missing or has a different digest"):
        _receipt_hash(
            receipt,
            kind="usage",
            snapshot_id="pin-20261017T120000Z",
            ledger_id="pin-20261017T120100Z",
            fenced_at=datetime(2026, 10, 17, 12, 1, tzinfo=UTC),
        )


def test_pre_fence_delivery_due_by_recovery_is_uncertain() -> None:
    class Result:
        def one_or_none(self):
            return (
                datetime(2026, 10, 17, 12, 1, tzinfo=UTC),
            )

    class Session:
        async def execute(self, _query):
            return Result()

    async def check(created_at: datetime, scheduled_at: datetime) -> bool:
        return await snapshot_delivery_is_uncertain(
            Session(),
            created_at=created_at,
            scheduled_at=scheduled_at,  # type: ignore[arg-type]
        )

    assert asyncio.run(
        check(
            datetime(2026, 10, 17, 11, 59, tzinfo=UTC),
            datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        )
    )
    # Rows written after the backup directory timestamp can still be inside pg_dump.
    assert asyncio.run(
        check(
            datetime(2026, 10, 17, 12, 0, 1, tzinfo=UTC),
            datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        )
    )

    async def generated_from_old_plan() -> bool:
        return await snapshot_delivery_is_uncertain(
            Session(),
            created_at=datetime(2026, 10, 17, 12, 0, 30, tzinfo=UTC),
            scheduled_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            source_created_at=datetime(2026, 10, 17, 12, 0, 59, tzinfo=UTC),
        )

    assert asyncio.run(generated_from_old_plan())

    async def generated_after_release_from_old_plan() -> bool:
        return await snapshot_delivery_is_uncertain(
            Session(),
            created_at=datetime(2026, 10, 17, 12, 2, tzinfo=UTC),
            scheduled_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
            source_created_at=datetime(2026, 10, 16, 12, 0, tzinfo=UTC),
            regenerated_occurrence=True,
        )

    assert asyncio.run(generated_after_release_from_old_plan())
    assert not asyncio.run(
        check(
            datetime(2026, 10, 17, 11, 59, tzinfo=UTC),
            datetime(2026, 10, 17, 12, 2, tzinfo=UTC),
        )
    )


def test_gateway_fence_rejects_before_bot_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    from pin.entrypoints import gateway
    from pin.platform.recovery import RecoveryFenced

    class Secret:
        def get_secret_value(self) -> str:
            return "postgresql+psycopg://unused"

    constructed = False

    def construct_bot(**_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("Bot must not be constructed while recovery is fenced")

    monkeypatch.setattr(
        gateway, "load_settings", lambda _role: SimpleNamespace(database_url=Secret())
    )
    monkeypatch.setattr(
        gateway,
        "require_runtime_recovery_ready",
        lambda _url: (_ for _ in ()).throw(RecoveryFenced("fenced")),
    )
    monkeypatch.setattr(gateway, "Bot", construct_bot)
    monkeypatch.setattr("sys.argv", ["pin-gateway"])

    with pytest.raises(SystemExit) as error:
        gateway.main()

    assert error.value.code == 2
    assert not constructed
