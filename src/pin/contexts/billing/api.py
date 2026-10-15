"""Published contracts for consumers of the billing context."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from pin.contexts.billing.infrastructure.operations import BillingOperations

from pin.contexts.billing.application.contracts import (
    AcceptPaymentTerms,
    AccessLifecycleStore,
    AccessNoticeQuietPolicy,
    AccessUsageStore,
    ActivatePaidPeriod,
    CheckAccess,
    CheckAccessNotice,
    CheckStarsCheckout,
    CreateStarsOrder,
    FinishStarsRefund,
    MarkAccountDeleted,
    ReadSubscriptionStatus,
    RecordStarsPayment,
    RequestStarsRefund,
    ReserveUsage,
    ReserveUsageResult,
    ResizeVoiceUsage,
    RestoreReturningPaidAccess,
    RetainBillingIdentity,
    StartTrial,
    SubscriptionStatus,
    TrialActivationResult,
    TrialActivationStatus,
    ViewPaymentTerms,
)
from pin.contexts.billing.domain.access import (
    AccessDecision,
    AccessDenial,
    AccessNoticeAuthorization,
    AccessNoticeKind,
    AccessNoticeStatus,
    AccountLifecycle,
    Allowance,
    IdempotencyConflict,
    InvalidTransition,
    Operation,
    PeriodKind,
    PlanPolicy,
    QuotaExceeded,
    ReservationState,
    UsageReservation,
    authorize_access_notice,
)
from pin.contexts.billing.domain.payments import (
    STARS_CURRENCY,
    STARS_PERIOD,
    STARS_PERIOD_SECONDS,
    STARS_PRICE,
    TERMS_VERSION,
)
from pin.contexts.billing.infrastructure.payments import (
    CheckoutDecision,
    PaymentInvoice,
    PaymentRejected,
    SqlAlchemyStarsPaymentStore,
)
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore

__all__ = [
    "AccessLifecycleStore",
    "AccountLifecycle",
    "AccessDecision",
    "AccessDenial",
    "AccessNoticeAuthorization",
    "AccessNoticeKind",
    "AccessNoticeQuietPolicy",
    "AccessNoticeStatus",
    "AccessUsageStore",
    "AcceptPaymentTerms",
    "ActivatePaidPeriod",
    "Allowance",
    "CheckAccessNotice",
    "CheckStarsCheckout",
    "CheckAccess",
    "MarkAccountDeleted",
    "CreateStarsOrder",
    "CheckoutDecision",
    "FinishStarsRefund",
    "IdempotencyConflict",
    "InvalidTransition",
    "Operation",
    "PeriodKind",
    "PlanPolicy",
    "PaymentInvoice",
    "PaymentRejected",
    "QuotaExceeded",
    "ReservationState",
    "ReserveUsage",
    "ReserveUsageResult",
    "ResizeVoiceUsage",
    "SqlAlchemyAccessUsageStore",
    "StartTrial",
    "TrialActivationResult",
    "TrialActivationStatus",
    "UsageReservation",
    "ReadSubscriptionStatus",
    "RecordStarsPayment",
    "RetainBillingIdentity",
    "RestoreReturningPaidAccess",
    "RequestStarsRefund",
    "STARS_CURRENCY",
    "STARS_PERIOD",
    "STARS_PERIOD_SECONDS",
    "STARS_PRICE",
    "SqlAlchemyStarsPaymentStore",
    "SubscriptionStatus",
    "TERMS_VERSION",
    "ViewPaymentTerms",
    "authorize_access_notice",
]


async def read_operational_projection(session: AsyncSession, *, at: datetime) -> BillingOperations:
    """Return billing-owned, redacted reservation and reconciliation facts."""
    from pin.contexts.billing.infrastructure.operations import read_billing_operations

    return await read_billing_operations(session, at=at)
