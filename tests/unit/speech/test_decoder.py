from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from pin.contexts.speech.domain.audio import UnusableAudio
from pin.contexts.speech.infrastructure.decoder import PyAVAudioDecoder


def _write_wav(path: Path, duration_seconds: float) -> None:
    source = Path(__file__).parents[2] / "fixtures" / "speech" / "en.wav"
    with wave.open(str(source), "rb") as audio:
        params = audio.getparams()
        frames = audio.readframes(audio.getnframes())
    frame_width = params.nchannels * params.sampwidth
    target_frames = round(duration_seconds * params.framerate)
    silence_frames = max(0, target_frames - len(frames) // frame_width)
    with wave.open(str(path), "wb") as audio:
        audio.setparams(params)
        audio.writeframes(frames)
        chunk_frames = 8192
        while silence_frames:
            count = min(silence_frames, chunk_frames)
            audio.writeframesraw(b"\0" * (count * frame_width))
            silence_frames -= count


@pytest.mark.parametrize("duration", [179.0, 180.0])
def test_decoder_measures_real_frames_at_supported_duration_boundary(
    tmp_path: Path, duration: float
) -> None:
    path = tmp_path / "bounded.wav"
    _write_wav(path, duration)
    decoded = asyncio.run(PyAVAudioDecoder().decode(path, size_bytes=path.stat().st_size))
    assert decoded.facts.duration_seconds == pytest.approx(duration, abs=0.001)
    assert decoded.waveform.shape[0] == round(duration * 16_000)


def test_decoder_rejects_audio_just_over_180_decoded_seconds(tmp_path: Path) -> None:
    path = tmp_path / "too-long.wav"
    _write_wav(path, 180.001)
    with pytest.raises(UnusableAudio, match="180 second"):
        asyncio.run(PyAVAudioDecoder().decode(path, size_bytes=path.stat().st_size))


def test_decoder_rejects_corrupt_input(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"not an audio container")
    with pytest.raises(UnusableAudio, match="could not be decoded"):
        asyncio.run(PyAVAudioDecoder().decode(path, size_bytes=path.stat().st_size))


def test_decoder_rechecks_actual_file_size_against_download_receipt(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "fixtures" / "speech" / "en.wav"
    path = tmp_path / "changed.wav"
    path.write_bytes(source.read_bytes())
    with pytest.raises(UnusableAudio, match="size changed"):
        asyncio.run(PyAVAudioDecoder().decode(path, size_bytes=path.stat().st_size - 1))
