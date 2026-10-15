from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from pin.contexts.retention.domain.deletion import DeletionTombstone
from pin.contexts.retention.infrastructure.ledger import (
    append_tombstones,
    ensure_ledger,
    read_tombstones,
)


def test_empty_ledger_is_a_valid_replay_baseline(tmp_path: Path) -> None:
    artifact = tmp_path / "state" / "deletions.jsonl"
    ensure_ledger(artifact)

    assert artifact.is_file()
    assert artifact.stat().st_size == 0
    assert read_tombstones(artifact) == ()


def test_deletion_ledger_is_minimal_durable_and_idempotent(tmp_path: Path) -> None:
    artifact = tmp_path / "state" / "deletions.jsonl"
    item = DeletionTombstone(
        UUID("e0bf357f-ab13-4fb1-bcc2-c885f95f015a"),
        UUID("8ad80c42-91d1-4ad7-a326-40871c14e30f"),
        datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
    )

    append_tombstones(artifact, (item,))
    append_tombstones(artifact, (item,))

    assert read_tombstones(artifact) == (item,)
    payload = artifact.read_text(encoding="utf-8")
    assert payload.count("\n") == 1
    assert "telegram" not in payload
    assert "receipt" not in payload


def test_deletion_ledger_rejects_malformed_or_unknown_records(tmp_path: Path) -> None:
    artifact = tmp_path / "deletions.jsonl"
    artifact.write_text(
        '{"version":true,"event_id":"e0bf357f-ab13-4fb1-bcc2-c885f95f015a",'
        '"owner_id":"8ad80c42-91d1-4ad7-a326-40871c14e30f",'
        '"deleted_at":"2026-10-15T12:30:00+00:00"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 1"):
        read_tombstones(artifact)


def test_deletion_ledger_does_not_append_over_invalid_history(tmp_path: Path) -> None:
    artifact = tmp_path / "deletions.jsonl"
    artifact.write_text("not json\n", encoding="utf-8")
    item = DeletionTombstone(
        UUID("e0bf357f-ab13-4fb1-bcc2-c885f95f015a"),
        UUID("8ad80c42-91d1-4ad7-a326-40871c14e30f"),
        datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
    )

    with pytest.raises(ValueError):
        append_tombstones(artifact, (item,))
    assert artifact.read_text(encoding="utf-8") == "not json\n"


def test_crash_truncated_tail_fails_closed_before_any_replay_or_append(tmp_path: Path) -> None:
    artifact = tmp_path / "deletions.jsonl"
    valid = DeletionTombstone(
        UUID("e0bf357f-ab13-4fb1-bcc2-c885f95f015a"),
        UUID("8ad80c42-91d1-4ad7-a326-40871c14e30f"),
        datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
    )
    append_tombstones(artifact, (valid,))
    with artifact.open("ab") as stream:
        stream.write(b'{"version":1,"event_id":"unfinished')
    before = artifact.read_bytes()
    next_item = DeletionTombstone(
        UUID("514762f7-4383-4dc1-a99e-530dd11b077e"),
        UUID("c812c645-9040-4c69-96eb-b3a9b99597bf"),
        datetime(2026, 10, 15, 12, 31, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="line 2"):
        read_tombstones(artifact)
    with pytest.raises(ValueError, match="line 2"):
        append_tombstones(artifact, (next_item,))

    assert artifact.read_bytes() == before


def test_concurrent_ledger_writers_keep_records_whole_and_unique(tmp_path: Path) -> None:
    artifact = tmp_path / "deletions.jsonl"
    items = tuple(
        DeletionTombstone(
            UUID(int=index + 1),
            UUID(int=index + 100),
            datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
        )
        for index in range(12)
    )
    start = Barrier(len(items))

    def append(item: DeletionTombstone) -> None:
        start.wait()
        append_tombstones(artifact, (item,))

    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        tuple(executor.map(append, items))

    assert set(read_tombstones(artifact)) == set(items)


def test_concurrent_conflicting_event_id_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "deletions.jsonl"
    event_id = UUID("e0bf357f-ab13-4fb1-bcc2-c885f95f015a")
    items = (
        DeletionTombstone(
            event_id,
            UUID("8ad80c42-91d1-4ad7-a326-40871c14e30f"),
            datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
        ),
        DeletionTombstone(
            event_id,
            UUID("318dd0ab-1e61-4682-8ee8-491c633637a6"),
            datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
        ),
    )
    start = Barrier(2)

    def append(item: DeletionTombstone) -> str:
        start.wait()
        try:
            append_tombstones(artifact, (item,))
        except ValueError:
            return "conflict"
        return "written"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(append, items))

    assert sorted(outcomes) == ["conflict", "written"]
    assert len(read_tombstones(artifact)) == 1


def test_replay_fails_closed_when_artifact_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_tombstones(tmp_path / "missing.jsonl")
