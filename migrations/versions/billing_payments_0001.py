"""Add Telegram Stars terms, orders, receipts, and manual refund audit."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "billing_payments_0001"
down_revision: str | None = "notification_access_resume_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terms_acceptances",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("terms_version", sa.String(length=64), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(terms_version) BETWEEN 1 AND 64", name="ck_billing_terms_version"
        ),
        sa.PrimaryKeyConstraint("owner_id", "terms_version"),
        schema="billing",
    )
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("invoice_payload", sa.String(length=128), nullable=False),
        sa.Column("terms_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("pre_checkout_query_id", sa.String(length=128), nullable=True),
        sa.Column("invoice_status", sa.String(length=16), nullable=False),
        sa.Column("invoice_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invoice_message_id", sa.BigInteger(), nullable=True),
        sa.Column("invoice_error_code", sa.String(length=64), nullable=True),
        sa.Column("first_charge_id", sa.String(length=128), nullable=True),
        sa.Column("latest_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewal_cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_status", sa.String(length=16), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_billing_order_telegram_user"),
        sa.CheckConstraint(
            "state IN ('open', 'prechecked', 'paid', 'cancelled')", name="ck_billing_order_state"
        ),
        sa.CheckConstraint(
            "length(invoice_payload) BETWEEN 1 AND 128", name="ck_billing_order_payload"
        ),
        sa.CheckConstraint(
            "invoice_status IN ('queued', 'sending', 'sent', 'failed', 'unknown')",
            name="ck_billing_order_invoice_status",
        ),
        sa.CheckConstraint(
            "cancel_status IS NULL OR cancel_status IN "
            "('queued', 'sending', 'confirmed', 'failed', 'unknown')",
            name="ck_billing_order_cancel_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["billing.access_accounts.owner_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_payload", name="uq_billing_order_payload"),
        sa.UniqueConstraint("pre_checkout_query_id", name="uq_billing_order_pre_checkout_query"),
        schema="billing",
    )
    op.create_index(
        "uq_billing_open_order_owner",
        "payment_orders",
        ["owner_id"],
        unique=True,
        schema="billing",
        postgresql_where=sa.text("state IN ('open', 'prechecked')"),
    )
    op.create_table(
        "payment_receipts",
        sa.Column("telegram_charge_id", sa.String(length=128), nullable=False),
        sa.Column("provider_charge_id", sa.String(length=128), nullable=True),
        sa.Column("order_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("is_recurring", sa.Boolean(), nullable=False),
        sa.Column("is_first_recurring", sa.Boolean(), nullable=False),
        sa.Column("period_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("currency = 'XTR'", name="ck_billing_receipt_currency"),
        sa.CheckConstraint("total_amount = 250", name="ck_billing_receipt_amount"),
        sa.CheckConstraint("is_recurring", name="ck_billing_receipt_recurring"),
        sa.ForeignKeyConstraint(["order_id"], ["billing.payment_orders.id"]),
        sa.PrimaryKeyConstraint("telegram_charge_id"),
        sa.UniqueConstraint("provider_charge_id", name="uq_billing_provider_charge"),
        schema="billing",
    )
    op.create_index(
        "ix_billing_receipt_owner_time",
        "payment_receipts",
        ["owner_id", "received_at"],
        schema="billing",
    )
    op.create_table(
        "payment_refunds",
        sa.Column("telegram_charge_id", sa.String(length=128), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('requested', 'confirmed', 'failed', 'unknown')",
            name="ck_billing_refund_status",
        ),
        sa.CheckConstraint(
            "length(requested_by) BETWEEN 1 AND 128", name="ck_billing_refund_operator"
        ),
        sa.ForeignKeyConstraint(
            ["telegram_charge_id"], ["billing.payment_receipts.telegram_charge_id"]
        ),
        sa.PrimaryKeyConstraint("telegram_charge_id"),
        schema="billing",
    )


def downgrade() -> None:
    op.drop_table("payment_refunds", schema="billing")
    op.drop_index("ix_billing_receipt_owner_time", table_name="payment_receipts", schema="billing")
    op.drop_table("payment_receipts", schema="billing")
    op.drop_index("uq_billing_open_order_owner", table_name="payment_orders", schema="billing")
    op.drop_table("payment_orders", schema="billing")
    op.drop_table("terms_acceptances", schema="billing")
