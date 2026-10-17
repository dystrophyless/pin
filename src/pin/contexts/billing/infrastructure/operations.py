"""Billing-owned reconciliation counts that omit account and provider identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.infrastructure.models import (
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
    UsageReservationRecord,
)
from pin.contexts.billing.infrastructure.payments import paid_period_id


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
    active_paid_period_count: int
    active_paid_period_traces: tuple[dict[str, object], ...]


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
    active_paid_period_count = int(
        await session.scalar(
            select(func.count()).select_from(AccessPeriodRecord).where(
                AccessPeriodRecord.policy_kind == "paid",
                AccessPeriodRecord.starts_at <= at,
                AccessPeriodRecord.ends_at > at,
            )
        )
        or 0
    )
    active_period_rows = tuple(
        await session.execute(
            select(AccessPeriodRecord.owner_id, AccessPeriodRecord.period_id)
            .where(
                AccessPeriodRecord.policy_kind == "paid",
                AccessPeriodRecord.starts_at <= at,
                AccessPeriodRecord.ends_at > at,
            )
            .order_by(AccessPeriodRecord.ends_at, AccessPeriodRecord.period_id)
            .limit(25)
        )
    )
    period_keys = tuple((owner_id, period_id) for owner_id, period_id in active_period_rows)
    order_by_period: dict[tuple[object, str], str] = {}
    receipt_candidates_count = 0
    if period_keys:
        owners = tuple({owner_id for owner_id, _ in period_keys})
        receipt_filter = (
            PaymentReceiptRecord.owner_id.in_(owners),
            PaymentReceiptRecord.period_starts_at <= at,
            PaymentReceiptRecord.period_ends_at > at,
        )
        receipt_candidates_count = int(
            await session.scalar(
                select(func.count()).select_from(PaymentReceiptRecord).where(*receipt_filter)
            )
            or 0
        )
        receipts = await session.execute(
            select(
                PaymentReceiptRecord.owner_id,
                PaymentReceiptRecord.telegram_charge_id,
                PaymentReceiptRecord.order_id,
            )
            .where(*receipt_filter)
            .order_by(PaymentReceiptRecord.received_at.desc())
            .limit(25)
        )
        period_key_set = set(period_keys)
        for owner_id, charge_id, order_id in receipts:
            candidate_period_id = paid_period_id(charge_id)
            key = (owner_id, candidate_period_id)
            if key in period_key_set:
                order_by_period[key] = str(order_id)

    reservation_states_by_period: dict[tuple[object, str], dict[str, int]] = {
        key: {} for key in period_keys
    }
    reservation_counts = await session.execute(
        select(
            UsageReservationRecord.owner_id,
            UsageReservationRecord.period_id,
            UsageReservationRecord.state,
            func.count(),
        )
        .where(
            tuple_(UsageReservationRecord.owner_id, UsageReservationRecord.period_id).in_(
                period_keys
            )
        )
        .group_by(
            UsageReservationRecord.owner_id,
            UsageReservationRecord.period_id,
            UsageReservationRecord.state,
        )
    )
    for owner_id, period_id, state, count in reservation_counts:
        reservation_states_by_period[(owner_id, period_id)][state] = int(count)

    reservations_by_period: dict[tuple[object, str], list[dict[str, str]]] = {
        key: [] for key in period_keys
    }
    reservation_ids = await session.execute(
        select(
            UsageReservationRecord.owner_id,
            UsageReservationRecord.period_id,
            UsageReservationRecord.id,
            UsageReservationRecord.state,
        )
        .where(
            tuple_(UsageReservationRecord.owner_id, UsageReservationRecord.period_id).in_(
                period_keys
            )
        )
        .order_by(UsageReservationRecord.created_at.desc(), UsageReservationRecord.id)
        .limit(25)
    )
    for owner_id, period_id, reservation_id, state in reservation_ids:
        reservations_by_period[(owner_id, period_id)].append(
            {"reservation_id": str(reservation_id), "state": state}
        )

    paid_period_traces = tuple(
        {
            "period_id": period_id,
            "order_id": order_by_period.get((owner_id, period_id)),
            "receipt_linked": (
                (owner_id, period_id) in order_by_period
                if receipt_candidates_count <= 25
                else None
                if (owner_id, period_id) not in order_by_period
                else True
            ),
            "reservation_states": reservation_states_by_period[(owner_id, period_id)],
            "reservations": reservations_by_period[(owner_id, period_id)],
        }
        for owner_id, period_id in period_keys
    )
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
        active_paid_period_count=active_paid_period_count,
        active_paid_period_traces=paid_period_traces,
    )
