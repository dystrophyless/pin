"""Planning-owned durable mutation previews."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class MutationPreviewRecord(Base):
    __tablename__ = "mutation_previews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_mutation_preview_occurrence_owner",
        ),
        CheckConstraint("preview_version >= 1", name="ck_planning_mutation_preview_version"),
        CheckConstraint(
            "action IN ('complete', 'cancel', 'reschedule')",
            name="ck_planning_mutation_preview_action",
        ),
        CheckConstraint(
            "state IN ('pending', 'confirmed', 'cancelled')",
            name="ck_planning_mutation_preview_state",
        ),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    occurrence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preferences_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    projection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
