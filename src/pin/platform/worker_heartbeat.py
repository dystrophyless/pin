"""Platform-owned worker liveness records shared by process roles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from pin.platform.models import WorkerHeartbeat

logger = logging.getLogger("pin.operations")
HEARTBEAT_INTERVAL_SECONDS = 15


async def write_worker_heartbeat(
    sessions: async_sessionmaker[AsyncSession], *, worker_id: str, role: str, stopped: bool = False
) -> None:
    if (
        not worker_id
        or len(worker_id) > 120
        or role
        not in {
            "application",
            "delivery",
            "speech",
            "gateway",
        }
    ):
        raise ValueError("invalid worker heartbeat identity")
    async with sessions() as session, session.begin():
        statement = insert(WorkerHeartbeat).values(
            worker_id=worker_id,
            role=role,
            started_at=func.now(),
            last_seen_at=func.now(),
            stopped_at=func.now() if stopped else None,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_id],
                set_={"last_seen_at": func.now(), "stopped_at": func.now() if stopped else None},
            )
        )


@asynccontextmanager
async def worker_heartbeat(
    sessions: async_sessionmaker[AsyncSession], *, worker_id: str, role: str
) -> AsyncIterator[None]:
    await write_worker_heartbeat(sessions, worker_id=worker_id, role=role)
    stopped = asyncio.Event()

    async def pulse() -> None:
        while True:
            try:
                await asyncio.wait_for(stopped.wait(), HEARTBEAT_INTERVAL_SECONDS)
                return
            except TimeoutError:
                try:
                    await write_worker_heartbeat(sessions, worker_id=worker_id, role=role)
                except Exception as error:
                    logger.warning("worker_heartbeat_failed: error=%s", type(error).__name__)

    task = asyncio.create_task(pulse(), name=f"pin-heartbeat-{role}")
    try:
        yield
    finally:
        stopped.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await write_worker_heartbeat(sessions, worker_id=worker_id, role=role, stopped=True)
        except Exception as error:
            logger.warning("worker_heartbeat_stop_failed: error=%s", type(error).__name__)
