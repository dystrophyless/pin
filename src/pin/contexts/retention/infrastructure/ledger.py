"""Append-only local deletion artifact for replay after an older database snapshot."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

from pin.contexts.retention.domain.deletion import DeletionTombstone

_PROCESS_LOCKS: dict[str, Lock] = {}
_PROCESS_LOCKS_GUARD = Lock()


def _set_private_mode(descriptor: int) -> None:
    if os.name == "nt":
        return
    fchmod = getattr(os, "fchmod", None)
    if fchmod is None:
        raise OSError("descriptor permissions cannot be restricted on this platform")
    fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)


def _lock_posix(descriptor: int, *, unlock: bool = False) -> None:
    import fcntl

    operation_name = "LOCK_UN" if unlock else "LOCK_EX"
    flock = getattr(fcntl, "flock", None)
    operation = getattr(fcntl, operation_name, None)
    if flock is None or operation is None:
        raise OSError("POSIX ledger locking is unavailable")
    flock(descriptor, operation)


def ensure_ledger(path: Path) -> None:
    """Create an empty valid artifact before local maintenance is scheduled."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock(path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("deletion artifact must be a regular file")
            _set_private_mode(descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def append_tombstones(path: Path, tombstones: tuple[DeletionTombstone, ...]) -> None:
    """Append minimal replay facts and flush them before any operational purge."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock(path):
        existing = {item.event_id: item for item in _read_tombstones(path)} if path.exists() else {}
        pending: list[DeletionTombstone] = []
        for item in tombstones:
            prior = existing.get(item.event_id)
            if prior is not None:
                if prior != item:
                    raise ValueError("deletion event conflicts with local replay history")
                continue
            existing[item.event_id] = item
            pending.append(item)
        if not pending:
            return
        _append_locked(path, pending)


def _append_locked(path: Path, pending: list[DeletionTombstone]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    descriptor = os.open(path, flags, 0o600)
    try:
        _set_private_mode(descriptor)
        for item in pending:
            record = {
                "version": 1,
                "event_id": str(item.event_id),
                "owner_id": str(item.owner_id),
                "deleted_at": item.deleted_at.isoformat(),
            }
            payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("deletion ledger accepted a partial write")
                view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_tombstones(path: Path) -> tuple[DeletionTombstone, ...]:
    """Parse the complete artifact before replay; reject unknown or malformed records."""
    if not path.exists():
        raise FileNotFoundError(path)
    with _ledger_lock(path):
        return _read_tombstones(path)


def _read_tombstones(path: Path) -> tuple[DeletionTombstone, ...]:
    result: dict[UUID, DeletionTombstone] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            raw: Any = json.loads(line)
            if not isinstance(raw, dict) or set(raw) != {
                "version",
                "event_id",
                "owner_id",
                "deleted_at",
            }:
                raise ValueError
            if type(raw["version"]) is not int or raw["version"] != 1:
                raise ValueError
            item = DeletionTombstone(
                event_id=UUID(raw["event_id"]),
                owner_id=UUID(raw["owner_id"]),
                deleted_at=datetime.fromisoformat(raw["deleted_at"]),
            )
            if item.owner_id.int == 0 or item.event_id.int == 0:
                raise ValueError
            if item.deleted_at.tzinfo is None or item.deleted_at.utcoffset() is None:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid deletion tombstone at line {line_number}") from error
        prior = result.get(item.event_id)
        if prior is not None and prior != item:
            raise ValueError(f"conflicting deletion tombstone at line {line_number}")
        result[item.event_id] = item
    return tuple(result[key] for key in sorted(result, key=str))


@contextmanager
def _ledger_lock(path: Path) -> Iterator[None]:
    """Serialize local writers/readers across threads and processes."""
    lock_path = path.with_name(f"{path.name}.lock")
    lock_key = str(lock_path.resolve())
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(lock_key, Lock())
    process_lock.acquire()
    descriptor: int | None = None
    locked = False
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        _set_private_mode(descriptor)
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            locked = True
        else:
            _lock_posix(descriptor)
            locked = True
        yield
    finally:
        try:
            if descriptor is not None and locked:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    _lock_posix(descriptor, unlock=True)
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                process_lock.release()
