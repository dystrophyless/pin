"""Durable Planning-owned previews for owner-confirmed home-zone changes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class HomeZoneChangePreviewRecord(Base):
    __tablename__ = "home_zone_change_previews"
    __table_args__ = (
        CheckConstraint("profile_version >= 1", name="ck_planning_zone_preview_profile_version"),
        CheckConstraint("preview_version >= 1", name="ck_planning_zone_preview_version"),
        CheckConstraint("length(projection_sha256) = 64", name="ck_planning_zone_preview_digest"),
        CheckConstraint(
            "state IN ('pending', 'confirmed', 'cancelled')",
            name="ck_planning_zone_preview_state",
        ),
        Index("ix_planning_zone_preview_owner", "owner_id"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    projection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
