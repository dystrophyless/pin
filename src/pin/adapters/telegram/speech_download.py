"""Download a trusted Telegram voice file into bounded private storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

from pin.contexts.speech.domain.audio import MAX_AUDIO_BYTES, UnusableAudio
from pin.contexts.speech.infrastructure.files import (
    BoundedAudioWriter,
    DownloadLimitExceeded,
    PrivateAudioFiles,
)


class TelegramFile(Protocol):
    file_path: str | None
    file_size: int | None


class TelegramFileBot(Protocol):
    async def get_file(self, file_id: str) -> TelegramFile: ...

    async def download_file(
        self,
        file_path: str | Path,
        destination: BinaryIO | Path | str | None = None,
        timeout: int = 30,
        chunk_size: int = 65_536,
        seek: bool = True,
    ) -> BinaryIO | None: ...


@dataclass(frozen=True, slots=True)
class DownloadedAudio:
    path: Path
    size_bytes: int


async def download_voice_file(
    bot: TelegramFileBot,
    *,
    file_id: str,
    files: PrivateAudioFiles,
    owner_id: UUID | None = None,
) -> DownloadedAudio:
    """Resolve only Telegram's trusted file ID and enforce actual streamed bytes."""
    if not file_id or len(file_id) > 1024:
        raise UnusableAudio("Telegram voice file reference is invalid")
    remote = await bot.get_file(file_id)
    if not remote.file_path:
        raise UnusableAudio("Telegram did not provide a voice file path")
    if remote.file_size is not None:
        if type(remote.file_size) is not int or remote.file_size <= 0:
            raise UnusableAudio("Telegram voice file size is invalid")
        if remote.file_size > MAX_AUDIO_BYTES:
            raise UnusableAudio("audio exceeds the 10 MB limit")

    path, destination = files.create(owner_id=owner_id)
    writer = BoundedAudioWriter(destination)
    try:
        try:
            await bot.download_file(remote.file_path, destination=writer, seek=False)
            writer.flush()
        except DownloadLimitExceeded as error:
            raise UnusableAudio(str(error)) from error
        if writer.bytes_written == 0:
            raise UnusableAudio("Telegram returned an empty audio file")
        return DownloadedAudio(path, writer.bytes_written)
    except BaseException:
        destination.close()
        files.remove(path)
        raise
    finally:
        if not destination.closed:
            destination.close()
