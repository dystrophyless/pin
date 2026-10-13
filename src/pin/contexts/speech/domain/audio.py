"""Speech input bounds and measured audio facts."""

from __future__ import annotations

import math
from dataclasses import dataclass

MAX_AUDIO_BYTES = 10_000_000
MAX_AUDIO_SECONDS = 180


class UnusableAudio(ValueError):
    """Audio is outside the product's supported processing contract."""


@dataclass(frozen=True, slots=True)
class AudioFacts:
    size_bytes: int
    duration_seconds: float

    def __post_init__(self) -> None:
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise UnusableAudio("audio size must be a positive byte count")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds <= 0
        ):
            raise UnusableAudio("audio duration must be a positive finite number")
        if self.size_bytes > MAX_AUDIO_BYTES:
            raise UnusableAudio("audio exceeds the 10 MB limit")
        if self.duration_seconds > MAX_AUDIO_SECONDS:
            raise UnusableAudio("audio exceeds the 180 second limit")

    @property
    def billable_seconds(self) -> int:
        """Round up fractional seconds so quota cannot undercount decoded audio."""
        return math.ceil(self.duration_seconds)


def validate_declared_audio(size_bytes: object, duration_seconds: object) -> None:
    """Reject Telegram-declared oversize before download; values remain untrusted."""
    if size_bytes is not None:
        _positive_int(size_bytes, "declared audio size")
        if type(size_bytes) is int and size_bytes > MAX_AUDIO_BYTES:
            raise UnusableAudio("audio exceeds the 10 MB limit")
    if duration_seconds is not None:
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or duration_seconds <= 0
        ):
            raise UnusableAudio("declared audio duration is invalid")
        if duration_seconds > MAX_AUDIO_SECONDS:
            raise UnusableAudio("audio exceeds the 180 second limit")


def validate_transcript(text: str, language: str) -> tuple[str, str]:
    transcript = text.strip()
    normalized_language = language.strip().lower()
    if not transcript:
        raise UnusableAudio("no speech was recognized")
    if normalized_language not in {"ru", "en"}:
        raise UnusableAudio("only Russian and English speech is supported")
    return transcript, normalized_language


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise UnusableAudio(f"{name} is invalid")
    return value
