"""Retention-owned deletion workflow and replay receipts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class DeletionRequestRecord(Base):
    __tablename__ = "deletion_requests"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_retention_deletion_request_version"),
        CheckConstraint(
            "state IN ('previewed', 'cancelled', 'pending_export', 'completed')",
            name="ck_retention_deletion_request_state",
        ),
        Index("ix_retention_deletion_owner_state", "owner_id", "state"),
        {"schema": "retention"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="previewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletionTombstoneRecord(Base):
    """Pseudonymous replay fact retained without an arbitrary expiry."""

    __tablename__ = "deletion_tombstones"
    __table_args__ = (
        CheckConstraint("artifact_version = 1", name="ck_retention_tombstone_artifact_version"),
        Index("ix_retention_tombstone_export", "exported_at"),
        {"schema": "retention"},
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
