"""The pinned local multilingual Whisper-small CPU/INT8 adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from pin.contexts.speech.application.contracts import DecodedAudio, SpeechRecognition
from pin.contexts.speech.domain.audio import validate_transcript


class FasterWhisperSmall:
    def __init__(self, model: Any) -> None:
        """Adapt an already-loaded backend; importing/loading belongs to bootstrap."""
        self._model = model

    async def transcribe(self, audio: DecodedAudio) -> SpeechRecognition:
        return await asyncio.to_thread(self._transcribe, audio)

    def _transcribe(self, audio: DecodedAudio) -> SpeechRecognition:
        segments, info = self._model.transcribe(
            audio.waveform,
            task="transcribe",
            language=None,
            beam_size=5,
            temperature=0,
            no_speech_threshold=0.6,
            vad_filter=True,
        )
        text = "".join(segment.text for segment in segments).strip()
        transcript, language = validate_transcript(text, info.language or "")
        # Billing and persistence use PyAV's measured decoded facts, never model metadata.
        return SpeechRecognition(transcript, language, audio.facts)
