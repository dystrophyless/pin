"""Notifications-owned, payload-free send outcome projection."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.notifications.infrastructure.models import (
    AccountabilityDeliveryAttemptRecord,
    DeliveryAttemptRecord,
)


@dataclass(frozen=True, slots=True)
class DeliveryOperations:
    outcomes: dict[str, int]
    unknown_attempt_ids: tuple[str, ...]
    started_attempts: tuple[dict[str, str], ...]


async def read_delivery_operations(session: AsyncSession) -> DeliveryOperations:
    outcomes: dict[str, int] = {}
    unknown_ids: list[str] = []
    started: list[dict[str, str]] = []
    for model in (DeliveryAttemptRecord, AccountabilityDeliveryAttemptRecord):
        grouped = await session.execute(select(model.outcome, func.count()).group_by(model.outcome))
        for outcome, count in grouped:
            outcomes[outcome] = outcomes.get(outcome, 0) + int(count)
        unknown_ids.extend(
            str(value)
            for value in await session.scalars(
                select(model.id)
                .where(model.outcome == "unknown")
                .order_by(model.started_at.desc())
                .limit(25)
            )
        )
        started.extend(
            {"attempt_id": str(attempt_id), "started_at": started_at.isoformat()}
            for attempt_id, started_at in await session.execute(
                select(model.id, model.started_at)
                .where(model.outcome == "started")
                .order_by(model.started_at)
                .limit(25)
            )
        )
    return DeliveryOperations(
        outcomes=outcomes,
        unknown_attempt_ids=tuple(unknown_ids[:25]),
        started_attempts=tuple(started[:25]),
    )
