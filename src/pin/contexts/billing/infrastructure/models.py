"""Billing-owned PostgreSQL records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
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
    deletion_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TrialActivationRecord(Base):
    """Minimal trial history survives account removal to prevent trial reset on return."""

    __tablename__ = "trial_activations"
    __table_args__ = {"schema": "billing"}

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetainedBillingIdentityRecord(Base):
    """Minimal actor-to-owner link retained for access restoration after account deletion."""

    __tablename__ = "retained_return_owners"
    __table_args__ = {"schema": "billing"}

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    retained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class AccessExpiryNoticeRecord(Base):
    """One scheduled warning per access period and lead window."""

    __tablename__ = "access_expiry_notices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "period_id"],
            ["billing.access_periods.owner_id", "billing.access_periods.period_id"],
            ondelete="CASCADE",
            name="fk_billing_expiry_notice_period_owner",
        ),
        UniqueConstraint(
            "owner_id", "period_id", "kind", name="uq_billing_expiry_notice_period_kind"
        ),
        CheckConstraint(
            "kind IN ('expiry_24h', 'expiry_1h')", name="ck_billing_expiry_notice_kind"
        ),
        Index("ix_billing_expiry_notice_due", "emitted_at", "due_at"),
        {"schema": "billing"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    period_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    emitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class TermsAcceptanceRecord(Base):
    """Proof that the owner opened and explicitly accepted one terms revision."""

    __tablename__ = "terms_acceptances"
    __table_args__ = (
        CheckConstraint("length(terms_version) BETWEEN 1 AND 64", name="ck_billing_terms_version"),
        {"schema": "billing"},
    )

    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    terms_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentOrderRecord(Base):
    """One terms-bound Telegram subscription invoice and its provider identity."""

    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_billing_order_telegram_user"),
        CheckConstraint(
            "state IN ('open', 'prechecked', 'paid', 'cancelled')", name="ck_billing_order_state"
        ),
        CheckConstraint(
            "length(invoice_payload) BETWEEN 1 AND 128", name="ck_billing_order_payload"
        ),
        CheckConstraint(
            "invoice_status IN ('queued', 'sending', 'sent', 'failed', 'unknown')",
            name="ck_billing_order_invoice_status",
        ),
        CheckConstraint(
            "cancel_status IS NULL OR cancel_status IN "
            "('queued', 'sending', 'confirmed', 'failed', 'unknown')",
            name="ck_billing_order_cancel_status",
        ),
        ForeignKeyConstraint(["owner_id"], ["billing.access_accounts.owner_id"]),
        UniqueConstraint("invoice_payload", name="uq_billing_order_payload"),
        UniqueConstraint("pre_checkout_query_id", name="uq_billing_order_pre_checkout_query"),
        Index(
            "uq_billing_open_order_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("state IN ('open', 'prechecked')"),
        ),
        {"schema": "billing"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invoice_payload: Mapped[str] = mapped_column(String(128), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    pre_checkout_query_id: Mapped[str | None] = mapped_column(String(128))
    invoice_status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    invoice_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoice_message_id: Mapped[int | None] = mapped_column(BigInteger)
    invoice_error_code: Mapped[str | None] = mapped_column(String(64))
    first_charge_id: Mapped[str | None] = mapped_column(String(128))
    latest_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renewal_cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_status: Mapped[str | None] = mapped_column(String(16))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentReceiptRecord(Base):
    """Minimal immutable provider facts keyed by Telegram's charge identifier."""

    __tablename__ = "payment_receipts"
    __table_args__ = (
        CheckConstraint("currency = 'XTR'", name="ck_billing_receipt_currency"),
        CheckConstraint("total_amount = 250", name="ck_billing_receipt_amount"),
        CheckConstraint("is_recurring", name="ck_billing_receipt_recurring"),
        ForeignKeyConstraint(["order_id"], ["billing.payment_orders.id"]),
        Index("ix_billing_receipt_owner_time", "owner_id", "received_at"),
        {"schema": "billing"},
    )

    telegram_charge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_charge_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    order_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    is_recurring: Mapped[bool] = mapped_column(nullable=False)
    is_first_recurring: Mapped[bool] = mapped_column(nullable=False)
    period_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentRefundRecord(Base):
    """Manual full-refund operator request and confirmed Telegram outcome."""

    __tablename__ = "payment_refunds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'confirmed', 'failed', 'unknown')",
            name="ck_billing_refund_status",
        ),
        CheckConstraint(
            "length(requested_by) BETWEEN 1 AND 128", name="ck_billing_refund_operator"
        ),
        ForeignKeyConstraint(
            ["telegram_charge_id"], ["billing.payment_receipts.telegram_charge_id"]
        ),
        {"schema": "billing"},
    )

    telegram_charge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
