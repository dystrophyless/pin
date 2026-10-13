from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from pin.adapters.telegram.presentation.callbacks import (
    CallbackBinding,
    CallbackTokens,
    InvalidCallbackToken,
)
from pin.adapters.telegram.presentation.tokens import (
    SqlAlchemyCallbackTokenStore,
    TelegramCallbackTokenRecord,
)
from pin.persistence import run_local_async
from pin.persistence.db import create_engine, create_session_factory
from pin.persistence.uow import SqlAlchemyUnitOfWork

DATABASE_URL = os.getenv("PIN_FORMATTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set PIN_FORMATTING_TEST_DATABASE_URL to a migrated disposable PostgreSQL database",
)


def test_callback_binding_survives_new_sessions_and_replay_is_rejected() -> None:
    assert DATABASE_URL is not None

    async def scenario() -> None:
        engine = create_engine(DATABASE_URL)
        session_factory = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime.now(UTC)
        issued: list[str] = []

        try:
            async with SqlAlchemyUnitOfWork(session_factory) as uow:
                service = CallbackTokens(SqlAlchemyCallbackTokenStore(uow.session))
                token = await service.issue(
                    owner_id=owner_id,
                    action="complete",
                    target_id="occurrence-db-proof",
                    target_version=7,
                    expires_at=now + timedelta(minutes=5),
                    now=now,
                )
                issued.append(token)

        # A new UoW/session models a process restart; the DB retains the exact action binding.
            async with SqlAlchemyUnitOfWork(session_factory) as uow:
                service = CallbackTokens(SqlAlchemyCallbackTokenStore(uow.session))
                with pytest.raises(InvalidCallbackToken):
                    await service.consume(
                        token,
                        actor_id=uuid4(),
                        current_target_version=7,
                        now=now,
                    )
                binding = await service.consume(
                    token,
                    actor_id=owner_id,
                    current_target_version=7,
                    now=now,
                )
                assert binding is not None
                assert (
                    binding.owner_id,
                    binding.action,
                    binding.target_id,
                    binding.target_version,
                ) == (
                    owner_id,
                    "complete",
                    "occurrence-db-proof",
                    7,
                )

            async with SqlAlchemyUnitOfWork(session_factory) as uow:
                service = CallbackTokens(SqlAlchemyCallbackTokenStore(uow.session))
                with pytest.raises(InvalidCallbackToken):
                    await service.consume(
                        token,
                        actor_id=owner_id,
                        current_target_version=7,
                        now=now,
                    )
        finally:
            if issued:
                async with SqlAlchemyUnitOfWork(session_factory) as uow:
                    await uow.session.execute(
                        delete(TelegramCallbackTokenRecord).where(
                            TelegramCallbackTokenRecord.token_hash
                            == hashlib.sha256(issued[0].encode("ascii")).hexdigest()
                        )
                    )
            await engine.dispose()

    run_local_async(scenario)


def test_postgres_allows_only_one_concurrent_callback_consumer() -> None:
    assert DATABASE_URL is not None

    async def scenario() -> None:
        engine = create_engine(DATABASE_URL)
        session_factory = create_session_factory(engine)
        owner_id = uuid4()
        now = datetime.now(UTC)
        token_hash: str | None = None

        try:
            async with SqlAlchemyUnitOfWork(session_factory) as uow:
                service = CallbackTokens(SqlAlchemyCallbackTokenStore(uow.session))
                token = await service.issue(
                    owner_id=owner_id,
                    action="cancel",
                    target_id="occurrence-concurrent-proof",
                    target_version=2,
                    expires_at=now + timedelta(minutes=5),
                    now=now,
                )

            async def consume(issued_hash: str) -> CallbackBinding | None:
                async with SqlAlchemyUnitOfWork(session_factory) as uow:
                    result = await SqlAlchemyCallbackTokenStore(uow.session).consume(
                        token_hash=issued_hash,
                        owner_id=owner_id,
                        now=now,
                    )
                return result

            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            results = await asyncio.gather(
                consume(token_hash), consume(token_hash), consume(token_hash)
            )
            assert sum(result is not None for result in results) == 1
        finally:
            if token_hash:
                async with SqlAlchemyUnitOfWork(session_factory) as uow:
                    await uow.session.execute(
                        delete(TelegramCallbackTokenRecord).where(
                            TelegramCallbackTokenRecord.token_hash == token_hash
                        )
                    )
            await engine.dispose()

    run_local_async(scenario)
