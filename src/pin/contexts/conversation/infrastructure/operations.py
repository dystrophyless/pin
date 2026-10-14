"""Conversation-owned, payload-free model usage projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.conversation.infrastructure.models import ProviderCallRecord


@dataclass(frozen=True, slots=True)
class ProviderOperations:
    states: dict[str, int]
    unknown_call_ids: tuple[str, ...]
    started_call_ids: tuple[str, ...]
    reported_tokens: int
    reported_cost_usd: str


async def read_provider_operations(session: AsyncSession) -> ProviderOperations:
    rows = await session.execute(
        select(ProviderCallRecord.state, func.count()).group_by(ProviderCallRecord.state)
    )
    states = {state: int(count) for state, count in rows}
    unknown_ids = await session.scalars(
        select(ProviderCallRecord.call_id)
        .where(ProviderCallRecord.state == "unknown")
        .order_by(ProviderCallRecord.created_at.desc())
        .limit(25)
    )
    started_ids = await session.scalars(
        select(ProviderCallRecord.call_id)
        .where(ProviderCallRecord.state == "started")
        .order_by(ProviderCallRecord.created_at.desc())
        .limit(25)
    )
    tokens = await session.scalar(
        select(func.coalesce(func.sum(ProviderCallRecord.total_tokens), 0)).where(
            ProviderCallRecord.state == "reported"
        )
    )
    cost = await session.scalar(
        select(func.coalesce(func.sum(ProviderCallRecord.cost_usd), 0)).where(
            ProviderCallRecord.state == "reported"
        )
    )
    return ProviderOperations(
        states=states,
        unknown_call_ids=tuple(str(value) for value in unknown_ids),
        started_call_ids=tuple(str(value) for value in started_ids),
        reported_tokens=int(tokens or 0),
        reported_cost_usd=str(Decimal(cost or 0)),
    )
