"""Billing-owned reconciliation counts that omit account and provider identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.infrastructure.models import (
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
    UsageReservationRecord,
)


@dataclass(frozen=True, slots=True)
class BillingOperations:
    reservation_states: dict[str, int]
    overdue_reservation_ids: tuple[str, ...]
    invoice_states: dict[str, int]
    cancellation_states: dict[str, int]
    uncertain_order_ids: tuple[str, ...]
    refund_states: dict[str, int]
    uncertain_refund_order_ids: tuple[str, ...]
    receipt_count: int
    receipt_total_stars: int


async def read_billing_operations(session: AsyncSession, *, at: datetime) -> BillingOperations:
    reservation_rows = await session.execute(
        select(UsageReservationRecord.state, func.count()).group_by(UsageReservationRecord.state)
    )
    overdue = await session.scalars(
        select(UsageReservationRecord.id)
        .where(
            UsageReservationRecord.state == "reserved",
            UsageReservationRecord.expires_at <= at,
        )
        .order_by(UsageReservationRecord.expires_at)
        .limit(25)
    )
    invoice_rows = await session.execute(
        select(PaymentOrderRecord.invoice_status, func.count()).group_by(
            PaymentOrderRecord.invoice_status
        )
    )
    cancellation_rows = await session.execute(
        select(PaymentOrderRecord.cancel_status, func.count())
        .where(PaymentOrderRecord.cancel_status.is_not(None))
        .group_by(PaymentOrderRecord.cancel_status)
    )
    uncertain_orders = await session.scalars(
        select(PaymentOrderRecord.id)
        .where(
            (PaymentOrderRecord.invoice_status.in_(("sending", "unknown")))
            | (PaymentOrderRecord.cancel_status.in_(("sending", "unknown")))
        )
        .order_by(PaymentOrderRecord.created_at.desc())
        .limit(25)
    )
    refund_rows = await session.execute(
        select(PaymentRefundRecord.status, func.count()).group_by(PaymentRefundRecord.status)
    )
    uncertain_refunds = await session.scalars(
        select(PaymentOrderRecord.id)
        .join(PaymentReceiptRecord, PaymentReceiptRecord.order_id == PaymentOrderRecord.id)
        .join(
            PaymentRefundRecord,
            PaymentRefundRecord.telegram_charge_id == PaymentReceiptRecord.telegram_charge_id,
        )
        .where(PaymentRefundRecord.status == "unknown")
        .order_by(PaymentRefundRecord.requested_at.desc())
        .limit(25)
    )
    receipt_result = await session.execute(
        select(func.count(), func.coalesce(func.sum(PaymentReceiptRecord.total_amount), 0))
    )
    receipt_count, receipt_total = receipt_result.one()
    return BillingOperations(
        reservation_states={key: int(value) for key, value in reservation_rows},
        overdue_reservation_ids=tuple(str(value) for value in overdue),
        invoice_states={key: int(value) for key, value in invoice_rows},
        cancellation_states={key: int(value) for key, value in cancellation_rows},
        uncertain_order_ids=tuple(str(value) for value in uncertain_orders),
        refund_states={key: int(value) for key, value in refund_rows},
        uncertain_refund_order_ids=tuple(str(value) for value in uncertain_refunds),
        receipt_count=int(receipt_count),
        receipt_total_stars=int(receipt_total),
    )
