"""Private temporary speech files with bounded writes and crash cleanup."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tempfile
from collections.abc import Buffer
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from pin.contexts.speech.domain.audio import MAX_AUDIO_BYTES

_FILE_PREFIX = "audio-"
_FILE_SUFFIX = ".bin"
# Hourly cleanup can run nearly an hour after a file turns stale. Leave that
# scheduling margin so no crash leftover survives beyond the 24-hour promise.
_CRASH_TTL = timedelta(hours=23)


class DownloadLimitExceeded(ValueError):
    """A streamed Telegram file exceeded the actual byte limit."""


class PrivateAudioFiles:
    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            import getpass

            identity = hashlib.sha256(getpass.getuser().casefold().encode()).hexdigest()[:12]
            directory = Path(tempfile.gettempdir()) / f"pin-speech-{identity}"
        self.directory = directory

    @staticmethod
    def _owner_prefix(owner_id: UUID) -> str:
        identity = str(owner_id).encode("utf-8")
        return f"{_FILE_PREFIX}{hashlib.sha256(identity).hexdigest()[:16]}-"

    def create(self, *, owner_id: UUID | None = None) -> tuple[Path, BinaryIO]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        prefix = _FILE_PREFIX if owner_id is None else self._owner_prefix(owner_id)
        path = self.directory / f"{prefix}{uuid4().hex}{_FILE_SUFFIX}"
        return path, path.open("xb")

    def remove(self, path: Path) -> None:
        if path.parent == self.directory and path.name.startswith(_FILE_PREFIX):
            path.unlink(missing_ok=True)

    def remove_owner(self, owner_id: UUID) -> int:
        """Remove only regular generated audio files assigned to this owner."""
        if not self.directory.exists():
            return 0
        prefix = self._owner_prefix(owner_id)
        removed = 0
        for path in self.directory.iterdir():
            if not path.name.startswith(prefix) or path.suffix != _FILE_SUFFIX:
                continue
            try:
                if stat.S_ISREG(path.lstat().st_mode):
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def cleanup_crash_leftovers(self, *, now: datetime | None = None) -> int:
        """Delete only generated regular files older than the 24-hour crash bound."""
        if not self.directory.exists():
            return 0
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("cleanup clock must be timezone-aware")
        cutoff = instant.timestamp() - _CRASH_TTL.total_seconds()
        removed = 0
        for path in self.directory.iterdir():
            if path.name.startswith(_FILE_PREFIX) and path.suffix == _FILE_SUFFIX:
                try:
                    stat = path.lstat()
                    if stat.st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
        return removed


class _AudioRawWriter(io.RawIOBase):
    def __init__(self, destination: BinaryIO) -> None:
        super().__init__()
        self._destination = destination

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer) -> int:
        return self._destination.write(data)

    def flush(self) -> None:
        self._destination.flush()


class BoundedAudioWriter(io.BufferedWriter):
    """File-like adapter that stops the Telegram stream at the actual limit."""

    def __init__(self, destination: BinaryIO) -> None:
        super().__init__(_AudioRawWriter(destination), buffer_size=1)
        self.bytes_written = 0

    def write(self, data: Buffer) -> int:
        size = memoryview(data).nbytes
        next_size = self.bytes_written + size
        if next_size > MAX_AUDIO_BYTES:
            raise DownloadLimitExceeded("audio exceeds the 10 MB limit")
        written = super().write(data)
        if written != size:
            raise OSError("audio temporary file accepted a partial write")
        self.bytes_written = next_size
        return written

