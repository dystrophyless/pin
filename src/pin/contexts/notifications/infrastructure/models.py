"""Notifications-owned durable reminder intents and event receipts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class ReminderIntentRecord(Base):
    """A version-fenced reminder request ready for the delivery consumer."""

    __tablename__ = "reminder_intents"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "occurrence_id",
            "offset_kind",
            "offset_amount",
            name="uq_notifications_intent_semantic_key",
        ),
        UniqueConstraint("id", "owner_id", name="uq_notifications_intent_owner"),
        ForeignKeyConstraint(
            ["plan_item_id", "owner_id"],
            ["planning.plan_items.id", "planning.plan_items.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_plan_owner",
        ),
        ForeignKeyConstraint(
            ["occurrence_id", "owner_id"],
            ["planning.occurrences.id", "planning.occurrences.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_occurrence_owner",
        ),
        ForeignKeyConstraint(
            ["series_id", "owner_id"],
            ["planning.series.id", "planning.series.owner_id"],
            ondelete="CASCADE",
            name="fk_notifications_intent_series_owner",
        ),
        CheckConstraint("plan_version >= 1", name="ck_notifications_intent_plan_version"),
        CheckConstraint(
            "occurrence_version >= 1", name="ck_notifications_intent_occurrence_version"
        ),
        CheckConstraint(
            "preferences_version >= 1", name="ck_notifications_intent_preferences_version"
        ),
        CheckConstraint(
            "series_version IS NULL OR series_version >= 1",
            name="ck_notifications_intent_series_version",
        ),
        CheckConstraint("dispatch_version >= 1", name="ck_notifications_intent_dispatch_version"),
        CheckConstraint(
            "offset_kind IN ('calendar_days', 'elapsed_seconds')",
            name="ck_notifications_intent_offset_kind",
        ),
        CheckConstraint("offset_amount > 0", name="ck_notifications_intent_offset_amount"),
        CheckConstraint("source IN ('default', 'explicit')", name="ck_notifications_intent_source"),
        CheckConstraint(
            "state IN ('scheduled', 'invalidated')", name="ck_notifications_intent_state"
        ),
        CheckConstraint(
            "(series_id IS NULL AND series_version IS NULL) OR "
            "(series_id IS NOT NULL AND series_version IS NOT NULL)",
            name="ck_notifications_intent_series_pair",
        ),
        Index("ix_notifications_intents_due", "state", "due_at"),
        Index("ix_notifications_intents_owner_plan", "owner_id", "plan_item_id", "state"),
        {"schema": "notifications"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    series_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    preferences_version: Mapped[int] = mapped_column(Integer, nullable=False)
    series_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offset_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    offset_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="scheduled")
    dispatch_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NotificationReconciliationReceiptRecord(Base):
    """Idempotency receipt committed with each event-driven reconciliation."""

    __tablename__ = "reconciliation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "event_id", name="uq_notifications_receipt_owner_event"
        ),
        CheckConstraint(
            "status IN ('applied', 'stale')", name="ck_notifications_receipt_status"
        ),
        CheckConstraint("created_count >= 0", name="ck_notifications_receipt_created"),
        CheckConstraint("updated_count >= 0", name="ck_notifications_receipt_updated"),
        CheckConstraint("invalidated_count >= 0", name="ck_notifications_receipt_invalidated"),
        {"schema": "notifications"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalidated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
