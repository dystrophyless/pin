"""Bounded HTTP transport for the selected OpenRouter chat-completions route."""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import SecretStr

from pin.contexts.conversation.application.service import ROUTE, ProviderHTTPError

_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RESPONSE_BYTES = 128_000


class OpenRouterTransport:
    """Single-request transport; the API key is supplied by bootstrap, never read here."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        timeout_seconds: float = 25,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if not api_key.get_secret_value().strip():
            raise ValueError("OpenRouter API key is required")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5))
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_client = client is None

    async def complete(self, payload: dict[str, object]) -> dict[str, object]:
        if payload.get("model") != ROUTE:
            raise ValueError("only the selected Qwen route is permitted")
        async with self._client.stream(
            "POST",
            _URL,
            headers={
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise ProviderHTTPError(f"OpenRouter returned HTTP {response.status_code}")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise ProviderHTTPError("OpenRouter response exceeded the configured limit")
        try:
            data: Any = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderHTTPError("OpenRouter returned a malformed response") from exc
        if not isinstance(data, dict):
            raise ProviderHTTPError("OpenRouter response must be a JSON object")
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
