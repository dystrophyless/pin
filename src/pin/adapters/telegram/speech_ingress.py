"""Extract the small, trusted Telegram voice reference from a private update."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TelegramVoiceAttachment:
    file_id: str | None
    declared_size_bytes: object
    declared_duration_seconds: object
    invalid_reason: str | None = None


def private_voice_attachment(payload: dict[str, Any]) -> TelegramVoiceAttachment | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    actor = message.get("from")
    voice = message.get("voice")
    if not isinstance(chat, dict) or not isinstance(actor, dict) or not isinstance(voice, dict):
        return None
    if chat.get("type") != "private" or actor.get("id") != chat.get("id"):
        return None

    file_id = voice.get("file_id")
    invalid_reason = None
    if not isinstance(file_id, str) or not file_id or len(file_id) > 1024:
        file_id = None
        invalid_reason = "invalid_file_reference"
    duration = voice.get("duration")
    if type(duration) is not int or duration <= 0:
        invalid_reason = invalid_reason or "invalid_declared_duration"
    size = voice.get("file_size")
    if size is not None and (type(size) is not int or size <= 0):
        invalid_reason = invalid_reason or "invalid_declared_size"
    return TelegramVoiceAttachment(file_id, size, duration, invalid_reason)
