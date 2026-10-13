from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pin.contexts.speech.domain.audio import MAX_AUDIO_BYTES
from pin.contexts.speech.infrastructure.files import (
    BoundedAudioWriter,
    DownloadLimitExceeded,
    PrivateAudioFiles,
)


@pytest.mark.parametrize("size", [MAX_AUDIO_BYTES - 1, MAX_AUDIO_BYTES])
def test_stream_byte_limit_accepts_below_and_exact_size(size: int) -> None:
    target = io.BytesIO()
    writer = BoundedAudioWriter(target)
    assert writer.write(b"x" * size) == size
    assert writer.bytes_written == size


def test_stream_byte_limit_rejects_actual_size_above_limit() -> None:
    target = io.BytesIO()
    writer = BoundedAudioWriter(target)
    writer.write(b"x" * MAX_AUDIO_BYTES)
    with pytest.raises(DownloadLimitExceeded):
        writer.write(b"x")
    assert writer.bytes_written == MAX_AUDIO_BYTES


def test_crash_cleanup_removes_only_old_generated_audio_files(tmp_path: Path) -> None:
    files = PrivateAudioFiles(tmp_path / "audio")
    files.directory.mkdir()
    stale = files.directory / "audio-old.bin"
    recent = files.directory / "audio-new.bin"
    unrelated = files.directory / "other.bin"
    stale.write_bytes(b"audio")
    recent.write_bytes(b"audio")
    unrelated.write_bytes(b"keep")
    now = datetime.now(UTC)
    old = (now - timedelta(hours=24, minutes=1)).timestamp()
    recent_time = (now - timedelta(hours=1)).timestamp()
    os.utime(stale, (old, old))
    os.utime(recent, (recent_time, recent_time))

    assert files.cleanup_crash_leftovers(now=now) == 1
    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_crash_cleanup_scheduling_margin_meets_24_hour_bound(tmp_path: Path) -> None:
    files = PrivateAudioFiles(tmp_path / "audio")
    files.directory.mkdir()
    stale = files.directory / "audio-stale.bin"
    fresh = files.directory / "audio-fresh.bin"
    stale.write_bytes(b"audio")
    fresh.write_bytes(b"audio")
    now = datetime.now(UTC)
    stale_time = (now - timedelta(hours=23, minutes=1)).timestamp()
    fresh_time = (now - timedelta(hours=22, minutes=59)).timestamp()
    os.utime(stale, (stale_time, stale_time))
    os.utime(fresh, (fresh_time, fresh_time))

    assert files.cleanup_crash_leftovers(now=now) == 1
    assert not stale.exists()
    assert fresh.exists()
