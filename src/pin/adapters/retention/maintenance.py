"""Local scheduled maintenance and pre-worker replay composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.retention.deletion import (
    TEXT_RETENTION_PURGERS,
    finish_pending_deletions,
    purge_expired_text_once,
    replay_deletion_artifact,
)
from pin.adapters.telegram.deletion import purge_account
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles


@dataclass(frozen=True, slots=True)
class RetentionMaintenanceResult:
    completed_deletions: int
    expired_text_rows: int
    crashed_audio_files: int


async def run_retention_once(
    sessions: async_sessionmaker[AsyncSession],
    *,
    artifact: Path,
    now: datetime,
    private_files: PrivateAudioFiles | None = None,
) -> RetentionMaintenanceResult:
    """Drain confirmed deletion and all local TTL policies once without provider calls."""
    completed = await finish_pending_deletions(
        sessions, artifact=artifact, now=now, purge_owner=purge_account
    )
    expired = await purge_expired_text_once(sessions, now=now, purgers=TEXT_RETENTION_PURGERS)
    audio_files = private_files or PrivateAudioFiles()
    audio = await asyncio.to_thread(audio_files.cleanup_crash_leftovers, now=now)
    return RetentionMaintenanceResult(completed, expired, audio)


async def replay_before_workers(
    sessions: async_sessionmaker[AsyncSession], *, artifact: Path, now: datetime
) -> int:
    """Fail closed if the recovery artifact is missing or malformed."""
    return await replay_deletion_artifact(
        sessions, artifact=artifact, now=now, purge_owner=purge_account
    )
