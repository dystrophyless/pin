"""Published, redacted operational view of the speech context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class SpeechOperations:
    states: dict[str, int]
    failed_job_ids: tuple[str, ...]


async def read_operational_projection(session: AsyncSession) -> SpeechOperations:
    """Summarize voice requests without reading transcripts, language, or request keys."""
    from pin.contexts.speech.infrastructure.operations import read_speech_operations

    return await read_speech_operations(session)
