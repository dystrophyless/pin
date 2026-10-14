"""Accountability-owned check-in, review and durable send-intent records."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from pin.persistence.models import Base


class CheckInCycleRecord(Base):
    __tablename__ = "check_in_cycles"
    __table_args__ = (
        CheckConstraint("kind IN ('appointment', 'deadline')", name="ck_accountability_cycle_kind"),
        CheckConstraint(
            "state IN ('open', 'completed', 'cancelled', 'superseded', 'exhausted')",
            name="ck_accountability_cycle_state",
        ),
        CheckConstraint("occurrence_version >= 1", name="ck_accountability_cycle_version"),
        Index("ix_accountability_cycle_due", "state", "expected_end_at"),
        Index("ix_accountability_cycle_owner_occurrence", "owner_id", "occurrence_id", "state"),
        {"schema": "accountability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurrence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    initial_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudge_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudge_slot_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    nudge_intent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DailyReviewRecord(Base):
    __tablename__ = "daily_reviews"
    __table_args__ = (
        CheckConstraint(
            "state IN ('open', 'completed', 'cancelled', 'superseded', 'exhausted')",
            name="ck_accountability_review_state",
        ),
        UniqueConstraint("owner_id", "review_date", name="uq_accountability_review_owner_date"),
        Index("ix_accountability_review_due", "state", "due_at"),
        {"schema": "accountability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    zone_name: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    destination_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurrence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudge_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudge_slot_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    nudge_intent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AccountabilityIntentRecord(Base):
    __tablename__ = "delivery_intents"
    __table_args__ = (
        CheckConstraint("slot IN ('initial', 'nudge')", name="ck_accountability_intent_slot"),
        CheckConstraint(
            "(cycle_id IS NOT NULL AND review_id IS NULL) OR "
            "(cycle_id IS NULL AND review_id IS NOT NULL)",
            name="ck_accountability_intent_subject",
        ),
        CheckConstraint(
            "state IN ('scheduled', 'sending', 'confirmed', 'failed', 'unknown', "
            "'skipped', 'exhausted')",
            name="ck_accountability_intent_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_accountability_intent_attempts"),
        CheckConstraint("dispatch_version >= 1", name="ck_accountability_intent_dispatch_version"),
        CheckConstraint(
            "last_outcome_attempt >= 0", name="ck_accountability_intent_outcome_attempt"
        ),
        Index("uq_accountability_intent_cycle_slot", "cycle_id", "slot", unique=True),
        Index("uq_accountability_intent_review_slot", "review_id", "slot", unique=True),
        Index("ix_accountability_intent_due", "state", "due_at"),
        {"schema": "accountability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    review_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    slot: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="scheduled")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_before_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_outcome_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retryable_after_access: Mapped[bool] = mapped_column(nullable=False, default=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AccountabilityOutcomeReceiptRecord(Base):
    __tablename__ = "outcome_receipts"
    __table_args__ = (
        UniqueConstraint("owner_id", "event_id", name="uq_accountability_outcome_owner_event"),
        {"schema": "accountability"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
