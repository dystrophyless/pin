"""Opaque, owner-bound Telegram callback tokens backed by a durable store."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

CALLBACK_DATA_MAX_BYTES = 64
MAX_CALLBACK_TOKEN_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class CallbackBinding:
    token_hash: str
    owner_id: UUID
    action: str
    target_id: str
    target_version: int
    expires_at: datetime


class CallbackTokenStore(Protocol):
    async def issue(self, binding: CallbackBinding) -> None: ...

    async def consume(
        self, token_hash: str, owner_id: UUID, now: datetime
    ) -> CallbackBinding | None: ...


class InvalidCallbackToken(ValueError):
    """The callback is unknown, unauthorized, expired, replayed, or stale."""


class CallbackTokens:
    def __init__(self, store: CallbackTokenStore) -> None:
        self._store = store

    async def issue(
        self,
        *,
        owner_id: UUID,
        action: str,
        target_id: str,
        target_version: int,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        if owner_id.int == 0 or not action or not target_id or target_version < 0:
            raise ValueError("callback claims are invalid")
        if now.utcoffset() is None or expires_at.utcoffset() is None:
            raise ValueError("callback times must be timezone-aware")
        if expires_at <= now or expires_at > now + MAX_CALLBACK_TOKEN_TTL:
            raise ValueError("callback expiry must be within 15 minutes")
        if len(action) > 64 or len(target_id) > 128:
            raise ValueError("callback claims exceed storage limits")

        token = secrets.token_urlsafe(18)
        if len(token.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
            raise RuntimeError("generated callback token exceeds Telegram's limit")
        await self._store.issue(
            CallbackBinding(
                token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                owner_id=owner_id,
                action=action,
                target_id=target_id,
                target_version=target_version,
                expires_at=expires_at.astimezone(UTC),
            )
        )
        return token

    async def consume(
        self,
        token: str,
        *,
        actor_id: UUID,
        current_target_version: int | None = None,
        current_target_versions: Mapping[str, int] | None = None,
        now: datetime,
    ) -> CallbackBinding:
        if (current_target_version is None) == (current_target_versions is None):
            raise ValueError("provide exactly one current target version policy")
        if (
            not token
            or len(token.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES
            or now.utcoffset() is None
        ):
            raise InvalidCallbackToken("invalid callback")
        binding = await self._store.consume(
            hashlib.sha256(token.encode("utf-8")).hexdigest(), actor_id, now.astimezone(UTC)
        )
        if binding is None:
            raise InvalidCallbackToken("invalid callback")
        expected_version = (
            current_target_versions.get(binding.action)
            if current_target_versions is not None
            else current_target_version
        )
        if expected_version is None or binding.target_version != expected_version:
            raise InvalidCallbackToken("invalid callback")
        return binding
