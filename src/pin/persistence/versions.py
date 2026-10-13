"""Database-enforced optimistic version updates for feature-owned tables."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Table, update
from sqlalchemy.ext.asyncio import AsyncSession


class OptimisticConcurrencyConflict(RuntimeError):
    """The row is missing or no longer has the expected version."""


async def update_versioned(
    session: AsyncSession,
    table: Table,
    identity: Any,
    expected_version: int,
    values: dict[str, Any],
) -> int:
    """Update one `id`/`version` row and atomically return its next version."""
    if expected_version < 1:
        raise ValueError("expected_version must be at least 1")
    if "id" not in table.c or "version" not in table.c:
        raise ValueError("versioned tables must define id and version columns")
    if "id" in values or "version" in values:
        raise ValueError("id and version are managed by update_versioned")

    statement = (
        update(table)
        .where(table.c.id == identity, table.c.version == expected_version)
        .values(**values, version=table.c.version + 1)
        .returning(table.c.version)
    )
    next_version = cast(int | None, (await session.execute(statement)).scalar_one_or_none())
    if next_version is None:
        raise OptimisticConcurrencyConflict("row changed or no longer exists")
    return next_version
