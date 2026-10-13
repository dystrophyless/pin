from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pin.adapters.telegram.speech_download import download_voice_file
from pin.adapters.telegram.speech_ingress import private_voice_attachment
from pin.contexts.speech.domain.audio import MAX_AUDIO_BYTES, UnusableAudio
from pin.contexts.speech.infrastructure.files import PrivateAudioFiles


class _RemoteFile:
    file_path = "trusted/from-telegram.oga"
    file_size = 1


class _Bot:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.file_ids: list[str] = []
        self.paths: list[str] = []

    async def get_file(self, file_id: str) -> _RemoteFile:
        self.file_ids.append(file_id)
        return _RemoteFile()

    async def download_file(self, file_path: str, destination: object, *, seek: bool) -> object:
        self.paths.append(file_path)
        destination.write(self.body)  # type: ignore[attr-defined]
        return destination


def test_private_voice_extraction_excludes_groups_and_keeps_only_file_facts() -> None:
    payload = {
        "message": {
            "chat": {"id": 41, "type": "private"},
            "from": {"id": 41},
            "voice": {"file_id": "opaque-telegram-id", "duration": 20, "file_size": 200},
        }
    }
    voice = private_voice_attachment(payload)
    assert voice is not None
    assert voice.file_id == "opaque-telegram-id"
    assert voice.declared_duration_seconds == 20
    assert voice.declared_size_bytes == 200
    payload["message"]["chat"]["type"] = "group"
    assert private_voice_attachment(payload) is None


def test_download_uses_only_get_file_result_and_enforces_actual_size(tmp_path: Path) -> None:
    bot = _Bot(b"safe audio")
    files = PrivateAudioFiles(tmp_path / "private")
    downloaded = asyncio.run(
        download_voice_file(bot, file_id="opaque-telegram-id", files=files)
    )
    assert bot.file_ids == ["opaque-telegram-id"]
    assert bot.paths == ["trusted/from-telegram.oga"]
    assert downloaded.path.read_bytes() == b"safe audio"
    assert downloaded.size_bytes == len(b"safe audio")


def test_smaller_declared_size_does_not_bypass_stream_limit(tmp_path: Path) -> None:
    bot = _Bot(b"x" * (MAX_AUDIO_BYTES + 1))
    files = PrivateAudioFiles(tmp_path / "private")
    with pytest.raises(UnusableAudio, match="10 MB"):
        asyncio.run(download_voice_file(bot, file_id="opaque-telegram-id", files=files))
    assert list(files.directory.iterdir()) == []
