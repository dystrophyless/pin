"""Read-only operational projection for platform work and worker health."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.platform.models import Job, RecoveryGate, WorkerHeartbeat

LANES = ("application", "speech", "delivery")
HEARTBEAT_STALE_AFTER_SECONDS = 45  # Three missed 15-second technical heartbeats.


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    generated_at: str
    database_ready: bool
    database_error: str | None
    lanes: dict[str, dict[str, int]]
    oldest_due_pending_seconds: float | None
    oldest_due_pending_job_ids: tuple[str, ...]
    dead_job_ids: tuple[str, ...]
    expired_lease_job_ids: tuple[str, ...]
    workers: tuple[dict[str, Any], ...]
    heartbeat_stale_after_seconds: int
    recovery: dict[str, str | None]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def read_operational_snapshot(session: AsyncSession) -> OperationalSnapshot:
    now = datetime.now(UTC)
    jobs = await session.execute(
        select(Job.lane, Job.status, func.count())
        .group_by(Job.lane, Job.status)
        .order_by(Job.lane, Job.status)
    )
    lane_counts: dict[str, dict[str, int]] = {lane: {} for lane in LANES}
    for lane, status, count in jobs:
        if lane in lane_counts:
            lane_counts[lane][status] = int(count)

    oldest_pending = await session.scalar(
        select(func.min(Job.created_at)).where(
            Job.status == "pending", Job.available_at <= func.now()
        )
    )
    oldest_pending_ids = tuple(
        str(job_id)
        for job_id in await session.scalars(
            select(Job.id)
            .where(Job.status == "pending", Job.available_at <= func.now())
            .order_by(Job.created_at, Job.id)
            .limit(25)
        )
    )
    dead_ids = tuple(
        str(job_id)
        for job_id in (
            await session.scalars(
                select(Job.id)
                .where(Job.status == "dead")
                .order_by(Job.finished_at.desc())
                .limit(25)
            )
        )
    )
    expired_lease_ids = tuple(
        str(job_id)
        for job_id in (
            await session.scalars(
                select(Job.id)
                .where(Job.status == "leased", Job.lease_until <= func.now())
                .order_by(Job.lease_until)
                .limit(25)
            )
        )
    )
    heartbeats = await session.execute(
        select(
            WorkerHeartbeat.worker_id,
            WorkerHeartbeat.role,
            WorkerHeartbeat.started_at,
            WorkerHeartbeat.last_seen_at,
            WorkerHeartbeat.stopped_at,
        )
        .order_by(WorkerHeartbeat.last_seen_at.desc())
        .limit(100)
    )
    gate = await session.execute(
        select(
            RecoveryGate.state,
            RecoveryGate.snapshot_id,
            RecoveryGate.ledger_id,
            RecoveryGate.fenced_at,
            RecoveryGate.released_at,
        ).where(RecoveryGate.singleton.is_(True))
    )
    gate_row = gate.one_or_none()
    workers = tuple(
        {
            "worker_id": worker_id,
            "role": role,
            "status": (
                "stopped"
                if stopped_at is not None
                else "stale"
                if (now - last_seen_at).total_seconds() > HEARTBEAT_STALE_AFTER_SECONDS
                else "running"
            ),
            "started_at": started_at.isoformat(),
            "last_seen_at": last_seen_at.isoformat(),
        }
        for worker_id, role, started_at, last_seen_at, stopped_at in heartbeats
    )
    age = (now - oldest_pending).total_seconds() if oldest_pending is not None else None
    return OperationalSnapshot(
        generated_at=now.isoformat(),
        database_ready=True,
        database_error=None,
        lanes=lane_counts,
        oldest_due_pending_seconds=max(0.0, age) if age is not None else None,
        oldest_due_pending_job_ids=oldest_pending_ids,
        dead_job_ids=dead_ids,
        expired_lease_job_ids=expired_lease_ids,
        workers=workers,
        heartbeat_stale_after_seconds=HEARTBEAT_STALE_AFTER_SECONDS,
        recovery=(
            {
                "state": gate_row.state,
                "snapshot_id": gate_row.snapshot_id,
                "ledger_id": gate_row.ledger_id,
                "fenced_at": gate_row.fenced_at.isoformat() if gate_row.fenced_at else None,
                "released_at": gate_row.released_at.isoformat()
                if gate_row.released_at
                else None,
            }
            if gate_row is not None
            else {
                "state": "unavailable",
                "snapshot_id": None,
                "ledger_id": None,
                "fenced_at": None,
                "released_at": None,
            }
        ),
    )
