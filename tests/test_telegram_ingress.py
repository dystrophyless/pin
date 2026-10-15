from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Bot
from aiogram.types import Update

from pin.adapters.telegram import polling
from pin.adapters.telegram.ingress import IngressReceipt, private_actor, validate_update
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


@pytest.mark.asyncio
async def test_polling_persists_aware_batch_receipt_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LockConnection:
        async def __aenter__(self) -> LockConnection:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def scalar(self, *_: object, **__: object) -> bool:
            return True

        async def commit(self) -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.record = SimpleNamespace(mode="polling", next_update_offset=0)

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def begin(self) -> Session:
            return self

        async def scalar(self, *_: object, **__: object) -> object:
            return self.record

    class Engine:
        def connect(self) -> LockConnection:
            return LockConnection()

    class SessionFactory:
        def __call__(self) -> Session:
            return Session()

    class FakeBot:
        async def get_updates(self, **_: object) -> list[dict[str, object]]:
            stop.set()
            return [_message_update(41, 42, text="Create a task in 2 hours")]

    stop = asyncio.Event()
    receipt_instants: list[datetime] = []

    async def select_mode(_session: object, *, bot_id: int, mode: str) -> object:
        assert bot_id == 123456 and mode == "polling"
        return SimpleNamespace(mode=mode, next_update_offset=0)

    async def persist(
        _session: object,
        *,
        bot_id: int,
        payload: dict[str, object],
        owner_resolver: object,
        received_at: datetime,
    ) -> IngressReceipt:
        assert bot_id == 123456
        assert owner_resolver is not None
        receipt_instants.append(received_at)
        return IngressReceipt(True, int(payload["update_id"]))

    monkeypatch.setattr(polling, "select_ingress_mode", select_mode)
    monkeypatch.setattr(polling, "validate_update", lambda update, _bot: update)
    monkeypatch.setattr(polling, "persist_update", persist)
    before = datetime.now(UTC)
    await polling.poll_updates(
        engine=Engine(),  # type: ignore[arg-type]
        sessions=SessionFactory(),  # type: ignore[arg-type]
        bot=FakeBot(),  # type: ignore[arg-type]
        bot_id=123456,
        stop=stop,
    )
    after = datetime.now(UTC)

    assert len(receipt_instants) == 1
    assert before <= receipt_instants[0] <= after
    assert receipt_instants[0].tzinfo is not None
    assert receipt_instants[0].utcoffset() == UTC.utcoffset(receipt_instants[0])
