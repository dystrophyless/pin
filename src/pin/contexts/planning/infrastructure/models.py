"""Planning-owned PostgreSQL records."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class PlanItemRecord(Base):
    __tablename__ = "plan_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('appointment', 'task', 'deadline', 'untimed')",
            name="ck_planning_plan_kind",
        ),
        CheckConstraint("version >= 1", name="ck_planning_plan_version"),
        UniqueConstraint("id", "owner_id", name="uq_planning_plan_owner"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OccurrenceRecord(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_occurrence_plan_owner",
        ),
        CheckConstraint("version >= 1", name="ck_planning_occurrence_version"),
        CheckConstraint(
            "state IN ('open', 'completed', 'cancelled')", name="ck_planning_occurrence_state"
        ),
        CheckConstraint("fold IS NULL OR fold IN ('earlier', 'later')", name="ck_planning_fold"),
        CheckConstraint(
            "end_fold IS NULL OR end_fold IN ('earlier', 'later')", name="ck_planning_end_fold"
        ),
        CheckConstraint(
            "(kind = 'untimed' AND local_time IS NULL AND starts_at IS NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NULL "
            "AND duration_seconds IS NULL) OR "
            "(kind = 'deadline' AND local_time IS NOT NULL AND starts_at IS NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NOT NULL "
            "AND duration_seconds IS NULL) OR "
            "(kind = 'appointment' AND local_time IS NOT NULL AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL AND deadline_at IS NULL AND duration_seconds > 0 "
            "AND ((end_local_date IS NULL AND end_local_time IS NULL) OR "
            "(end_local_date IS NOT NULL AND end_local_time IS NOT NULL))) OR "
            "(kind = 'task' AND local_time IS NOT NULL AND starts_at IS NOT NULL "
            "AND end_local_time IS NULL AND ends_at IS NULL AND deadline_at IS NULL "
            "AND (duration_seconds IS NULL OR duration_seconds > 0))",
            name="ck_planning_occurrence_schedule_shape",
        ),
        UniqueConstraint("id", "owner_id", name="uq_planning_occurrence_owner"),
        Index("ix_planning_occurrences_owner_local_date", "owner_id", "local_date", "kind"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    event_zone: Mapped[str] = mapped_column(String(128), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    local_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    fold: Mapped[str | None] = mapped_column(String(8))
    end_local_date: Mapped[date | None] = mapped_column(Date)
    end_local_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    end_fold: Mapped[str | None] = mapped_column(String(8))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reminder_preferences_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReminderRuleRecord(Base):
    __tablename__ = "reminder_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_planning_reminder_occurrence_owner",
        ),
        CheckConstraint(
            "offset_kind IN ('calendar_days', 'elapsed_seconds')",
            name="ck_planning_reminder_offset_kind",
        ),
        CheckConstraint("offset_amount > 0", name="ck_planning_reminder_offset_amount"),
        CheckConstraint("source IN ('explicit', 'default')", name="ck_planning_reminder_source"),
        UniqueConstraint(
            "occurrence_id", "offset_kind", "offset_amount", name="uq_planning_reminder_offset"
        ),
        Index("ix_planning_reminders_due_at", "due_at"),
        {"schema": "planning"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    offset_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    offset_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
