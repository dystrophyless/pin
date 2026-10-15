"""Transactional inbox, ordered jobs, and fenced PostgreSQL worker leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from pin.persistence.models import ActionReceipt
from pin.platform.models import (
    ConversationCursor,
    ConversationLease,
    InboxUpdate,
    Job,
)


class JobLane(StrEnum):
    APPLICATION = "application"
    SPEECH = "speech"
    DELIVERY = "delivery"


class LeaseLost(RuntimeError):
    """The worker's claim expired, was reclaimed, or was already finalized."""


@dataclass(frozen=True, slots=True)
class JobClaim:
    job_id: UUID
    lane: JobLane
    kind: str
    payload_version: int
    payload: dict[str, Any]
    owner_id: UUID | None
    conversation_sequence: int | None
    attempt: int
    worker_id: str
    token: UUID
    fencing_generation: int | None
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class InboxResult:
    accepted: bool
    inbox_id: UUID
    job_id: UUID | None


@dataclass(frozen=True, slots=True)
class JobEffectReceipt:
    first_application: bool
    result: dict[str, Any]


def _validate_work(
    lane: JobLane, kind: str, deduplication_key: str, lease_for: timedelta | None = None
) -> None:
    if not kind.strip() or len(kind) > 100:
        raise ValueError("kind must contain 1 to 100 characters")
    if not deduplication_key.strip() or len(deduplication_key) > 180:
        raise ValueError("deduplication_key must contain 1 to 180 characters")
    if lease_for is not None and lease_for <= timedelta(0):
        raise ValueError("lease_for must be positive")


async def enqueue_job(
    session: AsyncSession,
    *,
    lane: JobLane,
    kind: str,
    payload: dict[str, Any],
    deduplication_key: str,
    owner_id: UUID | None = None,
    ordered_conversation: bool = False,
    payload_version: int = 1,
    priority: int = 0,
    max_attempts: int = 8,
    claim: JobClaim | None = None,
) -> Job:
    """Add work in the caller's transaction; duplicate keys return the original job."""
    _validate_work(lane, kind, deduplication_key)
    if payload_version < 1 or max_attempts < 1:
        raise ValueError("payload_version and max_attempts must be positive")
    if ordered_conversation and (owner_id is None or lane is JobLane.DELIVERY):
        raise ValueError("ordered conversation work needs an owner and a non-delivery lane")
    if claim is not None:
        await lock_job_for_effects(session, claim)

    sequence: int | None = None
    if ordered_conversation:
        cursor = (
            insert(ConversationCursor)
            .values(owner_id=owner_id, next_sequence=1)
            .on_conflict_do_update(
                index_elements=[ConversationCursor.owner_id],
                set_={"next_sequence": ConversationCursor.next_sequence + 1},
            )
            .returning(ConversationCursor.next_sequence)
        )
        sequence = int((await session.execute(cursor)).scalar_one())

    statement = (
        insert(Job)
        .values(
            lane=lane.value,
            kind=kind,
            payload_version=payload_version,
            payload=payload,
            deduplication_key=deduplication_key,
            owner_id=owner_id,
            conversation_sequence=sequence,
            priority=priority,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(constraint="uq_platform_jobs_lane_dedupe")
        .returning(Job.id)
    )
    job_id = (await session.execute(statement)).scalar_one_or_none()
    if job_id is None:
        existing_job = await session.scalar(
            select(Job).where(Job.lane == lane.value, Job.deduplication_key == deduplication_key)
        )
        if existing_job is None:
            raise RuntimeError("deduplicated job was not visible in the current transaction")
        return existing_job
    return await session.get(Job, job_id, populate_existing=True)  # type: ignore[return-value]


async def receive_update(
    session: AsyncSession,
    *,
    bot_id: int,
    update_id: int,
    owner_id: UUID,
    update_type: str,
    payload: dict[str, Any],
    received_at: datetime | None = None,
    lane: JobLane = JobLane.APPLICATION,
    conversation_ordered: bool = True,
    priority: int = 0,
    max_attempts: int = 8,
) -> InboxResult:
    """Persist a transport receipt and its work item atomically in the caller's UoW."""
    receipt_instant = received_at or datetime.now(UTC)
    if receipt_instant.tzinfo is None or receipt_instant.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    if bot_id < 1 or update_id < 0:
        raise ValueError("bot_id and update_id are outside Telegram's valid range")
    if not update_type.strip() or len(update_type) > 80:
        raise ValueError("update_type must contain 1 to 80 characters")
    statement = (
        insert(InboxUpdate)
        .values(
            bot_id=bot_id,
            update_id=update_id,
            owner_id=owner_id,
            update_type=update_type,
            payload=payload,
            received_at=receipt_instant,
        )
        .on_conflict_do_nothing(constraint="uq_platform_inbox_bot_update")
        .returning(InboxUpdate.id)
    )
    inbox_id = (await session.execute(statement)).scalar_one_or_none()
    if inbox_id is None:
        existing = await session.scalar(
            select(InboxUpdate.id).where(
                InboxUpdate.bot_id == bot_id, InboxUpdate.update_id == update_id
            )
        )
        if existing is None:
            raise RuntimeError("duplicate inbox receipt was not visible in the current transaction")
        existing_job_id = await session.scalar(
            select(Job.id).where(
                Job.lane == lane.value,
                Job.deduplication_key == f"telegram:{bot_id}:{update_id}",
            )
        )
        return InboxResult(False, existing, existing_job_id)

    job = await enqueue_job(
        session,
        lane=lane,
        kind=f"conversation:{update_type}",
        payload={"inbox_id": str(inbox_id)},
        deduplication_key=f"telegram:{bot_id}:{update_id}",
        owner_id=owner_id,
        ordered_conversation=conversation_ordered,
        priority=priority,
        max_attempts=max_attempts,
    )
    return InboxResult(True, inbox_id, job.id)


def _claim_eligible(lane: JobLane) -> list[Any]:
    conditions = [
        Job.lane == lane.value,
        Job.available_at <= func.clock_timestamp(),
        or_(
            Job.status == "pending",
            and_(Job.status == "leased", Job.lease_until <= func.clock_timestamp()),
        ),
        Job.attempts < Job.max_attempts,
    ]
    if lane is not JobLane.DELIVERY:
        earlier_job = aliased(Job)
        earlier_work = (
            select(earlier_job.id)
            .where(
                earlier_job.owner_id == Job.owner_id,
                earlier_job.conversation_sequence < Job.conversation_sequence,
                earlier_job.status.not_in(("succeeded", "dead")),
            )
            .exists()
        )
        conditions.append(
            or_(
                Job.conversation_sequence.is_(None),
                and_(Job.owner_id.is_not(None), ~earlier_work),
            )
        )
        active_owner_lease = (
            select(ConversationLease.owner_id)
            .where(
                ConversationLease.owner_id == Job.owner_id,
                ConversationLease.lease_until > func.clock_timestamp(),
            )
            .exists()
        )
        conditions.append(
            or_(
                Job.conversation_sequence.is_(None),
                ~active_owner_lease,
            )
        )
    return conditions


async def claim_job(
    session: AsyncSession,
    *,
    lane: JobLane,
    worker_id: str,
    lease_for: timedelta,
) -> JobClaim | None:
    """Claim one due job with SKIP LOCKED and an owner-scoped fencing generation."""
    _validate_work(lane, "claim", "claim", lease_for)
    if not worker_id.strip() or len(worker_id) > 120:
        raise ValueError("worker_id must contain 1 to 120 characters")

    candidate = await session.execute(
        select(Job.id, Job.owner_id, Job.conversation_sequence)
        .where(*_claim_eligible(lane))
        .order_by(Job.priority.desc(), Job.available_at, Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Job)
    )
    selected = candidate.one_or_none()
    if selected is None:
        return None

    token = uuid4()
    generation: int | None = None
    if selected.owner_id is not None and selected.conversation_sequence is not None:
        owner_lease = (
            insert(ConversationLease)
            .values(
                owner_id=selected.owner_id,
                job_id=selected.id,
                worker_id=worker_id,
                lease_token=token,
                fencing_generation=1,
                lease_until=func.clock_timestamp() + lease_for,
            )
            .on_conflict_do_update(
                index_elements=[ConversationLease.owner_id],
                set_={
                    "job_id": selected.id,
                    "worker_id": worker_id,
                    "lease_token": token,
                    "fencing_generation": ConversationLease.fencing_generation + 1,
                    "lease_until": func.clock_timestamp() + lease_for,
                },
                where=ConversationLease.lease_until <= func.clock_timestamp(),
            )
            .returning(ConversationLease.fencing_generation)
        )
        generation = (await session.execute(owner_lease)).scalar_one_or_none()
        if generation is None:
            return None

    result = await session.execute(
        update(Job)
        .where(Job.id == selected.id)
        .values(
            status="leased",
            attempts=Job.attempts + 1,
            worker_id=worker_id,
            lease_token=token,
            lease_until=func.clock_timestamp() + lease_for,
            last_error=None,
        )
        .returning(Job)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise RuntimeError("locked job disappeared while it was being claimed")
    if job.lease_until is None:
        raise RuntimeError("claimed job has no lease expiry")
    return JobClaim(
        job_id=job.id,
        lane=lane,
        kind=job.kind,
        payload_version=job.payload_version,
        payload=job.payload,
        owner_id=job.owner_id,
        conversation_sequence=job.conversation_sequence,
        attempt=job.attempts,
        worker_id=worker_id,
        token=token,
        fencing_generation=generation,
        lease_until=job.lease_until,
    )


async def _lock_live_claim(session: AsyncSession, claim: JobClaim) -> None:
    query = select(Job.id).where(
        Job.id == claim.job_id,
        Job.status == "leased",
        Job.worker_id == claim.worker_id,
        Job.lease_token == claim.token,
        Job.lease_until > func.clock_timestamp(),
    )
    if claim.conversation_sequence is not None:
        query = (
            select(Job.id)
            .join(
                ConversationLease,
                and_(
                    ConversationLease.owner_id == Job.owner_id,
                    ConversationLease.job_id == Job.id,
                    ConversationLease.worker_id == claim.worker_id,
                    ConversationLease.lease_token == claim.token,
                    ConversationLease.lease_until > func.clock_timestamp(),
                ),
            )
            .where(
                Job.id == claim.job_id,
                Job.status == "leased",
                Job.worker_id == claim.worker_id,
                Job.lease_token == claim.token,
                Job.lease_until > func.clock_timestamp(),
            )
            .with_for_update(of=(Job, ConversationLease))
        )
    else:
        query = query.with_for_update(of=Job)
    if await session.scalar(query) is None:
        raise LeaseLost(f"job {claim.job_id} is no longer owned by this lease")


async def lock_job_for_effects(session: AsyncSession, claim: JobClaim) -> None:
    """Fence short business/usage/receipt writes in the same transaction as the claim check."""
    await _lock_live_claim(session, claim)


async def record_job_effect_once(
    session: AsyncSession,
    claim: JobClaim,
    *,
    effect_key: str,
    result: dict[str, Any],
) -> JobEffectReceipt:
    """Fence and receipt a business effect in the same transaction as its writes."""
    if claim.owner_id is None:
        raise ValueError("job effect receipts require owner-bound work")
    if not effect_key.strip() or len(effect_key) > 80:
        raise ValueError("effect_key must contain 1 to 80 characters")
    await lock_job_for_effects(session, claim)
    action_key = f"job:{claim.job_id}:{effect_key}"
    statement = (
        insert(ActionReceipt)
        .values(owner_id=claim.owner_id, action_key=action_key, result=result)
        .on_conflict_do_nothing(constraint="uq_action_receipts_owner_action")
        .returning(ActionReceipt.id)
    )
    receipt_id = (await session.execute(statement)).scalar_one_or_none()
    if receipt_id is not None:
        return JobEffectReceipt(True, result)
    saved = await session.scalar(
        select(ActionReceipt.result).where(
            ActionReceipt.owner_id == claim.owner_id,
            ActionReceipt.action_key == action_key,
        )
    )
    if saved is None:
        raise RuntimeError(
            "duplicate job effect receipt was not visible in the current transaction"
        )
    return JobEffectReceipt(False, saved)


async def renew_job_lease(
    session: AsyncSession, claim: JobClaim, *, lease_for: timedelta
) -> datetime:
    """Extend a live claim and its owner conversation lease atomically."""
    if lease_for <= timedelta(0):
        raise ValueError("lease_for must be positive")
    await _lock_live_claim(session, claim)
    expiry = func.clock_timestamp() + lease_for
    await session.execute(
        update(Job)
        .where(Job.id == claim.job_id, Job.lease_token == claim.token)
        .values(lease_until=expiry)
    )
    if claim.conversation_sequence is not None:
        await session.execute(
            update(ConversationLease)
            .where(
                ConversationLease.owner_id == claim.owner_id,
                ConversationLease.job_id == claim.job_id,
                ConversationLease.lease_token == claim.token,
            )
            .values(lease_until=expiry)
        )
    job = await session.get(Job, claim.job_id, populate_existing=True)
    if job is None or job.lease_until is None:
        raise LeaseLost(f"job {claim.job_id} disappeared during renewal")
    return job.lease_until


async def complete_job(session: AsyncSession, claim: JobClaim) -> None:
    """Complete exactly the still-live lease; stale workers cannot finalize reclaimed work."""
    await _lock_live_claim(session, claim)
    await session.execute(
        update(Job)
        .where(Job.id == claim.job_id, Job.lease_token == claim.token)
        .values(
            status="succeeded",
            finished_at=func.clock_timestamp(),
            worker_id=None,
            lease_token=None,
            lease_until=None,
            last_error=None,
        )
    )
    if claim.conversation_sequence is not None:
        await session.execute(
            update(ConversationLease)
            .where(
                ConversationLease.owner_id == claim.owner_id,
                ConversationLease.job_id == claim.job_id,
                ConversationLease.lease_token == claim.token,
            )
            .values(
                job_id=None,
                worker_id=None,
                lease_token=None,
                lease_until=func.clock_timestamp(),
            )
        )


async def fail_job(
    session: AsyncSession,
    claim: JobClaim,
    *,
    error: str,
    retry_after: timedelta = timedelta(seconds=2),
) -> str:
    """Schedule bounded exponential retry or mark poison work terminal."""
    if retry_after < timedelta(0):
        raise ValueError("retry_after cannot be negative")
    await _lock_live_claim(session, claim)
    job = await session.get(Job, claim.job_id, populate_existing=True)
    if job is None:
        raise LeaseLost(f"job {claim.job_id} disappeared")
    dead = job.attempts >= job.max_attempts
    multiplier = 2 ** min(max(job.attempts - 1, 0), 10)
    delay = min(retry_after * multiplier, timedelta(hours=1))
    await session.execute(
        update(Job)
        .where(Job.id == claim.job_id, Job.lease_token == claim.token)
        .values(
            status="dead" if dead else "pending",
            available_at=func.clock_timestamp() + delay,
            finished_at=func.clock_timestamp() if dead else None,
            worker_id=None,
            lease_token=None,
            lease_until=None,
            last_error=error[:1000],
        )
    )
    if claim.conversation_sequence is not None:
        await session.execute(
            update(ConversationLease)
            .where(
                ConversationLease.owner_id == claim.owner_id,
                ConversationLease.job_id == claim.job_id,
                ConversationLease.lease_token == claim.token,
            )
            .values(
                job_id=None,
                worker_id=None,
                lease_token=None,
                lease_until=func.clock_timestamp(),
            )
        )
    return "dead" if dead else "pending"
