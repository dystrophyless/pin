from types import SimpleNamespace

import pytest

from pin.adapters.telegram.speech_worker import _LazyWhisperRecognizer
from pin.contexts.speech.application.contracts import DecodedAudio
from pin.contexts.speech.domain.audio import AudioFacts, UnusableAudio
from pin.contexts.speech.infrastructure.whisper import FasterWhisperSmall


class Backend:
    def __init__(self) -> None:
        self.languages: list[str] = []

    def transcribe(
        self, waveform: object, **options: object
    ) -> tuple[list[SimpleNamespace], SimpleNamespace]:
        self.languages.append(str(options["language"]))
        # A backend's language guess must not override the explicit profile choice.
        return [SimpleNamespace(text=" reminder tomorrow ")], SimpleNamespace(language="sl")


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ru", "en"])
async def test_selected_language_reaches_backend_and_result(language: str) -> None:
    backend = Backend()
    recognizer = _LazyWhisperRecognizer()
    recognizer._recognizer = FasterWhisperSmall(backend)
    audio = DecodedAudio(AudioFacts(100, 1.0), object())
    result = await recognizer.transcribe(audio, language=language)
    assert backend.languages == [language]
    assert result.language == language
    assert result.text == "reminder tomorrow"
    assert result.audio == audio.facts


@pytest.mark.asyncio
async def test_unsupported_language_never_calls_backend() -> None:
    backend = Backend()
    with pytest.raises(UnusableAudio):
        await FasterWhisperSmall(backend).transcribe(
            DecodedAudio(AudioFacts(100, 1.0), object()), language="sl"
        )
    assert backend.languages == []
