"""Caller-UoW persistence for Telegram's in-progress mutation clarification."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Uuid,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class MutationRuntimePendingRecord(Base):
    """Owner-bound resumable target/schedule clarification, never a mutation."""

    __tablename__ = "mutation_runtime_pending"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_mutation_runtime_pending_version"),
        CheckConstraint(
            "expected_occurrence_version >= 1",
            name="ck_mutation_runtime_pending_occurrence_version",
        ),
        CheckConstraint(
            "expected_series_version IS NULL OR expected_series_version >= 1",
            name="ck_mutation_runtime_pending_series_version",
        ),
        CheckConstraint(
            "action IN ('complete', 'cancel', 'reschedule')",
            name="ck_mutation_runtime_pending_action",
        ),
        CheckConstraint(
            "scope IS NULL OR scope IN ('this_occurrence', 'this_and_future')",
            name="ck_mutation_runtime_pending_scope",
        ),
        CheckConstraint(
            "state IN ('awaiting_anchor', 'awaiting_scope', 'awaiting_schedule', "
            "'previewed', 'confirmed', 'dismissed', 'stale')",
            name="ck_mutation_runtime_pending_state",
        ),
        CheckConstraint("language IN ('en', 'ru')", name="ck_mutation_runtime_pending_language"),
        Index(
            "ix_mutation_runtime_pending_owner_state_expiry",
            "owner_id",
            "state",
            "expires_at",
        ),
        PrimaryKeyConstraint("id", name="pk_mutation_runtime_pending"),
        {"schema": "pin_telegram"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    expected_occurrence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_series_version: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    pending_fields: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SqlAlchemyMutationRuntimeStore:
    """Minimal persistence operations; caller owns commit/rollback."""

    _OPEN_STATES = ("awaiting_anchor", "awaiting_scope", "awaiting_schedule", "previewed")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self,
        *,
        owner_id: UUID,
        occurrence_id: UUID,
        expected_occurrence_version: int,
        expected_series_version: int | None = None,
        action: str,
        scope: str | None,
        state: str,
        language: str,
        pending_fields: dict[str, object],
        expires_at: datetime,
    ) -> MutationRuntimePendingRecord:
        await self._session.execute(
            update(MutationRuntimePendingRecord)
            .where(
                MutationRuntimePendingRecord.owner_id == owner_id,
                MutationRuntimePendingRecord.state.in_(self._OPEN_STATES),
            )
            .values(state="stale", version=MutationRuntimePendingRecord.version + 1)
        )
        record = MutationRuntimePendingRecord(
            id=uuid4(),
            owner_id=owner_id,
            occurrence_id=occurrence_id,
            expected_occurrence_version=expected_occurrence_version,
            expected_series_version=expected_series_version,
            action=action,
            scope=scope,
            version=1,
            state=state,
            language=language,
            pending_fields=pending_fields,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def get(
        self,
        *,
        owner_id: UUID,
        pending_id: UUID,
        lock: bool = False,
    ) -> MutationRuntimePendingRecord | None:
        statement = select(MutationRuntimePendingRecord).where(
            MutationRuntimePendingRecord.id == pending_id,
            MutationRuntimePendingRecord.owner_id == owner_id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return cast(MutationRuntimePendingRecord | None, await self._session.scalar(statement))

    async def get_latest_schedule(
        self,
        *,
        owner_id: UUID,
        at: datetime,
        lock: bool = False,
    ) -> MutationRuntimePendingRecord | None:
        statement = (
            select(MutationRuntimePendingRecord)
            .where(
                MutationRuntimePendingRecord.owner_id == owner_id,
                MutationRuntimePendingRecord.state == "awaiting_schedule",
                MutationRuntimePendingRecord.expires_at > at,
            )
            .order_by(
                MutationRuntimePendingRecord.updated_at.desc(),
                MutationRuntimePendingRecord.id,
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return cast(MutationRuntimePendingRecord | None, await self._session.scalar(statement))

    async def get_preview(
        self,
        *,
        owner_id: UUID,
        preview_id: UUID,
        lock: bool = False,
    ) -> MutationRuntimePendingRecord | None:
        statement = select(MutationRuntimePendingRecord).where(
            MutationRuntimePendingRecord.owner_id == owner_id,
            MutationRuntimePendingRecord.state == "previewed",
            MutationRuntimePendingRecord.pending_fields["preview_id"].as_string()
            == str(preview_id),
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return cast(MutationRuntimePendingRecord | None, await self._session.scalar(statement))

    async def update(
        self,
        record: MutationRuntimePendingRecord,
        *,
        state: str,
        scope: str | None = None,
        pending_fields: dict[str, object] | None = None,
        occurrence_id: UUID | None = None,
        expected_occurrence_version: int | None = None,
        expected_series_version: int | None = None,
    ) -> MutationRuntimePendingRecord:
        record.state = state
        record.scope = scope if scope is not None else record.scope
        if occurrence_id is not None:
            record.occurrence_id = occurrence_id
        if expected_occurrence_version is not None:
            record.expected_occurrence_version = expected_occurrence_version
        if expected_series_version is not None:
            record.expected_series_version = expected_series_version
        if pending_fields is not None:
            record.pending_fields = pending_fields
        record.version += 1
        await self._session.flush()
        return record


__all__ = ["MutationRuntimePendingRecord", "SqlAlchemyMutationRuntimeStore"]
