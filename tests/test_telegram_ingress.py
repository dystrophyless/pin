from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.types import Update

from pin.adapters.telegram.ingress import private_actor, validate_update
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings

BOT_TOKEN = "712345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"


def _message_update(update_id: int, user_id: int, **message_fields: Any) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1,
            "date": 1_760_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Test",
            },
            **message_fields,
        },
    }


def test_update_validation_discards_untrusted_owner_fields() -> None:
    bot = Bot(BOT_TOKEN)
    try:
        payload = _message_update(10, 42, text="hello", owner_id="forged")
        validated = validate_update(payload, bot)
        assert validated["message"]["text"] == "hello"
        assert validated["message"]["date"] == 1_760_000_000
        assert "owner_id" not in validated["message"]
    finally:
        asyncio.run(bot.session.close())


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (
            {
                "from": {"id": 42},
                "message": {
                    "chat": {"id": 42, "type": "private"},
                },
                "id": "callback-1",
            },
            (42, "callback_query", "callback-1"),
        ),
        (
            {
                "from": {"id": 42},
                "message": {
                    "chat": {"id": 42, "type": "group"},
                },
                "id": "callback-2",
            },
            None,
        ),
        (
            {
                "from": {"id": 42},
                "message": {
                    "chat": {"id": 99, "type": "private"},
                },
                "id": "callback-3",
            },
            None,
        ),
    ],
)
def test_callbacks_require_sender_bound_private_chat(
    update: dict[str, Any], expected: tuple[int, str, str | None] | None
) -> None:
    assert private_actor({"callback_query": update}) == expected


def test_gateway_polling_config_reads_only_dedicated_local_smoke_settings(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "PIN_BOT_TOKEN=712345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk\nPIN_TEST_CHAT_ID=42\n",
        encoding="utf-8",
    )
    settings = load_settings(
        ProcessRole.GATEWAY,
        {
            "PIN_DATABASE_URL": "postgresql+psycopg://pin@127.0.0.1/pin",
            "PIN_TELEGRAM_INGRESS_MODE": "polling",
        },
        project_root=tmp_path,
    )
    assert settings.bot_token is not None
    assert settings.bot_token.get_secret_value() == BOT_TOKEN
    assert settings.telegram_ingress_mode == "polling"


def test_polling_config_rejects_missing_smoke_settings_without_secret_echo(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text("PIN_TEST_CHAT_ID=42\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="PIN_BOT_TOKEN") as error:
        load_settings(
            ProcessRole.GATEWAY,
            {
                "PIN_DATABASE_URL": "postgresql+psycopg://pin@127.0.0.1/pin",
                "PIN_TELEGRAM_INGRESS_MODE": "polling",
            },
            project_root=tmp_path,
        )
    assert "712345678:" not in str(error.value)


def test_update_model_can_validate_with_transport_bot_fixture() -> None:
    bot = Bot(BOT_TOKEN)
    try:
        callback = {
            "update_id": 1,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 42, "is_bot": False, "first_name": "A"},
                "chat_instance": "instance",
                "message": {
                    "message_id": 2,
                    "date": 1_760_000_000,
                    "chat": {"id": 42, "type": "private"},
                },
                "data": "opaque-token",
            },
        }
        assert isinstance(Update.model_validate(callback, context={"bot": bot}), Update)
    finally:
        asyncio.run(bot.session.close())
