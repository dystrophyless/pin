"""Planning-owned persistent recurrence state and occurrence identity links."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class SeriesRecord(Base):
    """Mutable generation cursor for one immutable-owner recurrence series."""

    __tablename__ = "series"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_item_owner",
        ),
        CheckConstraint("version >= 1", name="ck_planning_series_version"),
        CheckConstraint("anchor_sequence >= 0", name="ck_planning_series_anchor_sequence"),
        CheckConstraint(
            "next_sequence >= anchor_sequence", name="ck_planning_series_next_sequence"
        ),
        CheckConstraint("state IN ('active', 'stopped')", name="ck_planning_series_state"),
        UniqueConstraint("id", "owner_id", name="uq_planning_series_owner"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    anchor_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int | None] = mapped_column(Integer)
    end_date: Mapped[date | None] = mapped_column(Date)
    rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    typed_draft: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SeriesOccurrenceRecord(Base):
    """Durable recurrence key for an ordinary, independently-stateful occurrence."""

    __tablename__ = "series_occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_occurrence_series_owner",
        ),
        ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_occurrence_plan_owner",
        ),
        CheckConstraint("sequence >= 0", name="ck_planning_series_occurrence_sequence"),
        CheckConstraint(
            "dst_gap_shift_seconds >= 0", name="ck_planning_series_gap_shift"
        ),
        CheckConstraint(
            "repeated_time_choice IS NULL OR repeated_time_choice = 'earlier'",
            name="ck_planning_series_repeated_choice",
        ),
        UniqueConstraint("series_id", "recurrence_key", name="uq_planning_series_recurrence_key"),
        UniqueConstraint("series_id", "sequence", name="uq_planning_series_sequence"),
        UniqueConstraint("occurrence_id", name="uq_planning_series_occurrence_id"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    recurrence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dst_gap_shift_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repeated_time_choice: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SeriesExceptionRecord(Base):
    """Immutable audit of an approved occurrence-only or this-and-future exception."""

    __tablename__ = "series_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_series_exception_series_owner",
        ),
        CheckConstraint("sequence >= 0", name="ck_planning_series_exception_sequence"),
        CheckConstraint("version >= 1", name="ck_planning_series_exception_version"),
        CheckConstraint(
            "scope IN ('single', 'this_and_future')", name="ck_planning_series_exception_scope"
        ),
        UniqueConstraint(
            "series_id", "sequence", "version", name="uq_planning_series_exception_version"
        ),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_change: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
