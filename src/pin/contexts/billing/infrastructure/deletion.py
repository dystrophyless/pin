"""Billing-owned deletion policy that keeps receipts and the TR-03 return facts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.domain.access import PeriodKind
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessExpiryNoticeRecord,
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    RetainedBillingIdentityRecord,
    TermsAcceptanceRecord,
    UsageReservationRecord,
)


async def purge_owner_operational_data(
    session: AsyncSession, *, owner_id: UUID, at: datetime
) -> None:
    """Remove holds and nonfinancial state while preserving receipts and active paid access."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("deletion time must be timezone-aware")
    account = await session.scalar(
        select(AccessAccountRecord)
        .where(AccessAccountRecord.owner_id == owner_id)
        .with_for_update()
    )
    if account is None or account.deleted_at is None or not account.deletion_pending:
        raise RuntimeError("Billing account is not in a confirmed deletion")

    active_paid = exists(
        select(AccessPeriodRecord.period_id).where(
            AccessPeriodRecord.owner_id == owner_id,
            AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
            AccessPeriodRecord.starts_at <= at,
            AccessPeriodRecord.ends_at > at,
        )
    )
    await session.execute(
        delete(UsageReservationRecord).where(UsageReservationRecord.owner_id == owner_id)
    )
    await session.execute(
        delete(AccessExpiryNoticeRecord).where(AccessExpiryNoticeRecord.owner_id == owner_id)
    )
    await session.execute(
        delete(TermsAcceptanceRecord).where(TermsAcceptanceRecord.owner_id == owner_id)
    )
    await session.execute(
        delete(AccessPeriodRecord).where(
            AccessPeriodRecord.owner_id == owner_id,
            ~(
                (AccessPeriodRecord.policy_kind == PeriodKind.PAID.value)
                & (AccessPeriodRecord.starts_at <= at)
                & (AccessPeriodRecord.ends_at > at)
            ),
        )
    )
    await session.execute(
        delete(PaymentOrderRecord).where(
            PaymentOrderRecord.owner_id == owner_id,
            ~exists(
                select(PaymentReceiptRecord.telegram_charge_id).where(
                    PaymentReceiptRecord.order_id == PaymentOrderRecord.id
                )
            ),
        )
    )
    if not await session.scalar(select(active_paid)):
        await session.execute(
            delete(RetainedBillingIdentityRecord).where(
                RetainedBillingIdentityRecord.owner_id == owner_id
            )
        )
    # TrialActivationRecord is deliberately retained as the TR-03 trial-used fact.
    from pin.contexts.billing.infrastructure.models import TrialActivationRecord  # noqa: F401
    await session.flush()

