from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.exc import SQLAlchemyError

from pin.adapters.telegram import webhook
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
