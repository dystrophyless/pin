"""Persistence-owned tables and shared SQLAlchemy metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata root for the single reviewed Alembic history."""


class ActionReceipt(Base):
    """Durable command result keyed by its server-derived owner and action key."""

    __tablename__ = "action_receipts"
    __table_args__ = (
        UniqueConstraint("owner_id", "action_key", name="uq_action_receipts_owner_action"),
        CheckConstraint(
            "length(btrim(action_key)) BETWEEN 1 AND 128",
            name="ck_action_receipts_action_key",
        ),
        {"schema": "pin_persistence"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
