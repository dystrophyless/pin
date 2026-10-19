from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.exc import SQLAlchemyError

from pin.adapters.telegram import ingress, webhook
from pin.adapters.telegram.ingress import IngressReceipt
from pin.adapters.telegram.webhook import create_webhook_app
from pin.persistence import run_local_async


class _Session:
    def __init__(self, failure: str | None) -> None:
        self.failure = failure

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, _query: Any) -> None:
        if self.failure == "database":
            raise SQLAlchemyError("private database diagnostic")
        if self.failure == "timeout":
            await asyncio.Future()


@pytest.mark.parametrize("failure", [None, "database", "timeout"])
def test_webhook_health_and_readiness_routes(
    failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if failure == "timeout":
        monkeypatch.setattr(webhook, "READINESS_TIMEOUT_SECONDS", 0.01)

    def sessions() -> _Session:
        return _Session(failure)

    app = create_webhook_app(
        engine=None,  # type: ignore[arg-type]
        sessions=sessions,  # type: ignore[arg-type]
        bot=Bot("123456:abcdefghijklmnopqrstuvwxyzABCDE123456"),
        bot_id=123456,
        webhook_secret="secret",
    )
    app.on_startup.clear()
    app.on_cleanup.clear()

    async def scenario() -> None:
        async with TestClient(TestServer(app)) as client:
            health = await client.get("/healthz")
            assert health.status == 200
            assert await health.json() == {"status": "alive"}

            readiness = await client.get("/readyz")
            if failure:
                assert readiness.status == 503
                assert await readiness.json() == {
                    "status": "not_ready",
                    "dependency": "database",
                }
                assert "private" not in await readiness.text()
            else:
                assert readiness.status == 200
                assert await readiness.json() == {"status": "ready"}

    run_local_async(scenario)


@pytest.mark.asyncio
async def test_webhook_persists_aware_handler_receipt_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed_transactions = 0

    class IngressSession:
        async def __aenter__(self) -> IngressSession:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def begin(self) -> _Transaction:
            return _Transaction()

    class _Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type: object, *_: object) -> None:
            nonlocal committed_transactions
            if exc_type is None:
                committed_transactions += 1

    received_instants: list[datetime] = []

    async def persist(
        _session: object,
        *,
        bot_id: int,
        payload: dict[str, Any],
        owner_resolver: object,
        received_at: datetime,
    ) -> IngressReceipt:
        assert bot_id == 123456
        assert owner_resolver is not None
        received_instants.append(received_at)
        return IngressReceipt(True, int(payload["update_id"]))

    monkeypatch.setattr(ingress, "persist_update", persist)
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDE123456")
    app = create_webhook_app(
        engine=None,  # type: ignore[arg-type]
        sessions=IngressSession,  # type: ignore[arg-type]
        bot=bot,
        bot_id=123456,
        webhook_secret="secret",
    )
    app.on_startup.clear()
    app.on_cleanup.clear()

    payload = {
        "update_id": 31,
        "message": {
            "message_id": 32,
            "date": 1_760_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Test"},
            "text": "Create a task in 2 hours",
        },
    }
    try:
        async with TestClient(TestServer(app)) as client:
            before = datetime.now(UTC)
            response = await client.post(
                "/telegram/webhook",
                json=payload,
                headers={webhook.SECRET_HEADER: "secret"},
            )
            after = datetime.now(UTC)
            assert response.status == 200
            assert await response.json() == {"ok": True}
        assert len(received_instants) == 1
        assert committed_transactions == 1
        assert before <= received_instants[0] <= after
        assert received_instants[0].tzinfo is not None
        assert received_instants[0].utcoffset() == UTC.utcoffset(received_instants[0])
    finally:
        await bot.session.close()
