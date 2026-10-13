"""Billing-owned PostgreSQL records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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


class AccessAccountRecord(Base):
    __tablename__ = "access_accounts"
    __table_args__ = {"schema": "billing"}

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialActivationRecord(Base):
    """Minimal trial history survives account removal to prevent trial reset on return."""

    __tablename__ = "trial_activations"
    __table_args__ = {"schema": "billing"}

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessPeriodRecord(Base):
    __tablename__ = "access_periods"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id"], ["billing.access_accounts.owner_id"], ondelete="CASCADE"
        ),
        CheckConstraint("policy_kind IN ('trial', 'paid')", name="ck_billing_period_policy_kind"),
        CheckConstraint("starts_at < ends_at", name="ck_billing_period_window"),
        CheckConstraint(
            "ai_turn_limit > 0 AND voice_second_limit > 0",
            name="ck_billing_period_allowance",
        ),
        Index("ix_billing_period_owner_window", "owner_id", "starts_at", "ends_at"),
        {"schema": "billing"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    period_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ai_turn_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_second_limit: Mapped[int] = mapped_column(Integer, nullable=False)


class UsageReservationRecord(Base):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "period_id"],
            ["billing.access_periods.owner_id", "billing.access_periods.period_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("owner_id", "request_id", name="uq_billing_usage_owner_request"),
        CheckConstraint(
            "state IN ('reserved', 'settled', 'refunded', 'expired')",
            name="ck_billing_usage_state",
        ),
        CheckConstraint(
            "reserved_ai_turns >= 0 AND reserved_voice_seconds >= 0 "
            "AND (reserved_ai_turns > 0 OR reserved_voice_seconds > 0)",
            name="ck_billing_usage_reserved_units",
        ),
        CheckConstraint(
            "settled_ai_turns >= 0 AND settled_voice_seconds >= 0 "
            "AND settled_ai_turns <= reserved_ai_turns "
            "AND settled_voice_seconds <= reserved_voice_seconds",
            name="ck_billing_usage_settled_units",
        ),
        CheckConstraint("created_at < expires_at", name="ck_billing_usage_expiry"),
        Index("ix_billing_usage_expiry", "state", "expires_at"),
        Index("ix_billing_usage_period_state", "owner_id", "period_id", "state"),
        {"schema": "billing"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_ai_turns: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_voice_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_ai_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settled_voice_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
