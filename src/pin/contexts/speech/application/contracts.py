"""Framework-independent speech processing ports and results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pin.contexts.speech.domain.audio import AudioFacts


@dataclass(frozen=True, slots=True)
class SpeechRecognition:
    text: str
    language: str
    audio: AudioFacts


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    facts: AudioFacts
    waveform: Any


class AudioDecoder(Protocol):
    async def decode(self, path: Path, *, size_bytes: int) -> DecodedAudio: ...


class SpeechRecognizer(Protocol):
    async def transcribe(self, audio: DecodedAudio) -> SpeechRecognition: ...
