"""Run real local Whisper-small recognition over the checked-in offline clips."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import wave
from pathlib import Path

from pin.bootstrap.speech import load_speech_model
from pin.contexts.speech.domain.audio import UnusableAudio
from pin.contexts.speech.infrastructure.decoder import PyAVAudioDecoder
from pin.contexts.speech.infrastructure.whisper import FasterWhisperSmall


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_dir = root / "tests" / "fixtures" / "speech"
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    decoder = PyAVAudioDecoder()
    recognizer = FasterWhisperSmall(await asyncio.to_thread(load_speech_model))
    expected = {"ru.wav": ("ru", "напоминание"), "en.wav": ("en", "reminder")}
    for filename, (language, keyword) in expected.items():
        path = fixture_dir / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if digest != manifest["voices"][filename]["sha256"]:
            raise AssertionError(f"fixture hash mismatch: {filename}")
        decoded = await decoder.decode(path, size_bytes=path.stat().st_size)
        recognized = await recognizer.transcribe(decoded)
        if recognized.language != language or keyword not in recognized.text.casefold():
            raise AssertionError(
                f"{filename}: expected {language}/{keyword}; "
                f"got {recognized.language}/{recognized.text!r}"
            )
        if abs(decoded.facts.duration_seconds - recognized.audio.duration_seconds) > 1:
            raise AssertionError(f"decoded duration disagrees with Whisper for {filename}")
        print(
            f"{filename}: language={recognized.language}, "
            f"decoded={decoded.facts.duration_seconds:.3f}s, "
            f"whisper={recognized.audio.duration_seconds:.3f}s, "
            f"text={recognized.text.encode('unicode_escape').decode('ascii')}"
        )

    with tempfile.TemporaryDirectory(prefix="pin-speech-silence-") as temporary:
        silent = Path(temporary) / "silence.wav"
        with wave.open(str(silent), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\0\0" * (16_000 * 3))
        decoded = await decoder.decode(silent, size_bytes=silent.stat().st_size)
        try:
            await recognizer.transcribe(decoded)
        except UnusableAudio:
            print("silence.wav: rejected as no recognized speech")
        else:
            raise AssertionError("silent audio unexpectedly produced a usable transcript")


if __name__ == "__main__":
    asyncio.run(main())
