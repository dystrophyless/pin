from __future__ import annotations

import math

import pytest

from pin.contexts.speech.domain.audio import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    AudioFacts,
    UnusableAudio,
    validate_declared_audio,
    validate_transcript,
)


@pytest.mark.parametrize(
    ("size", "duration"),
    [
        (MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS),
        (1, 179.01),
        (MAX_AUDIO_BYTES - 1, 1),
    ],
)
def test_actual_facts_accept_exact_limits_and_round_billable_seconds(
    size: int, duration: float
) -> None:
    facts = AudioFacts(size, duration)
    assert facts.billable_seconds == math.ceil(duration)


@pytest.mark.parametrize(
    ("size", "duration"),
    [
        (MAX_AUDIO_BYTES + 1, 1),
        (1, MAX_AUDIO_SECONDS + 0.001),
        (0, 1),
        (1, math.inf),
        (1, math.nan),
    ],
)
def test_actual_facts_reject_invalid_or_over_limit_audio(size: int, duration: float) -> None:
    with pytest.raises(UnusableAudio):
        AudioFacts(size, duration)


def test_declared_limits_are_checked_before_download() -> None:
    validate_declared_audio(MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS)
    with pytest.raises(UnusableAudio):
        validate_declared_audio(MAX_AUDIO_BYTES + 1, 1)
    with pytest.raises(UnusableAudio):
        validate_declared_audio(1, MAX_AUDIO_SECONDS + 1)


def test_only_nonempty_russian_and_english_transcripts_continue() -> None:
    assert validate_transcript("  привет  ", "RU") == ("привет", "ru")
    assert validate_transcript(" hello ", "en") == ("hello", "en")
    with pytest.raises(UnusableAudio):
        validate_transcript("   ", "en")
    with pytest.raises(UnusableAudio):
        validate_transcript("hello", "kk")
