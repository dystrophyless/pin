"""Bounded decoded-audio validation implemented with PyAV."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pin.contexts.speech.application.contracts import DecodedAudio
from pin.contexts.speech.domain.audio import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    AudioFacts,
    UnusableAudio,
)


class PyAVAudioDecoder:
    async def decode(self, path: Path, *, size_bytes: int) -> DecodedAudio:
        return await asyncio.to_thread(self._decode, path, size_bytes)

    @staticmethod
    def _decode(path: Path, size_bytes: int) -> DecodedAudio:
        if type(size_bytes) is not int or not 0 < size_bytes <= MAX_AUDIO_BYTES:
            raise UnusableAudio("audio size is outside the supported bounds")
        try:
            actual_size = path.stat().st_size
            if actual_size != size_bytes:
                raise UnusableAudio("downloaded audio size changed before decoding")
            import av
            import numpy as np

            with av.open(str(path), mode="r") as container:
                if not container.streams.audio:
                    raise UnusableAudio("file contains no audio stream")
                stream = container.streams.audio[0]
                sample_rate = stream.codec_context.sample_rate or stream.rate
                if not sample_rate:
                    raise UnusableAudio("audio sample rate is unavailable")
                resampler = av.AudioResampler(format="fltp", layout="mono", rate=16_000)
                samples: list[Any] = []
                duration = 0.0
                for frame in container.decode(stream):
                    rate = frame.sample_rate or sample_rate
                    if rate <= 0 or frame.samples < 0:
                        raise UnusableAudio("decoded audio frame is invalid")
                    duration += frame.samples / rate
                    if duration > MAX_AUDIO_SECONDS:
                        raise UnusableAudio("audio exceeds the 180 second limit")
                    samples.extend(item.to_ndarray() for item in resampler.resample(frame))
                samples.extend(item.to_ndarray() for item in resampler.resample(None))
                facts = AudioFacts(size_bytes, duration)
                if not samples:
                    raise UnusableAudio("file contains no decoded audio samples")
                waveform = (
                    np.concatenate(samples, axis=1).reshape(-1).astype(np.float32, copy=False)
                )
                return DecodedAudio(facts, waveform)
        except UnusableAudio:
            raise
        except Exception as error:
            raise UnusableAudio("audio could not be decoded") from error
