"""PostgreSQL transactions for Stars orders, receipts, and operator refunds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pin.contexts.billing.application.contracts import (
    AcceptPaymentTerms,
    ActivatePaidPeriod,
    CheckStarsCheckout,
    CreateStarsOrder,
    FinishStarsRefund,
    RecordStarsPayment,
    RequestStarsRefund,
    ViewPaymentTerms,
)
from pin.contexts.billing.domain.access import InvalidTransition, PeriodKind
from pin.contexts.billing.domain.payments import (
    STARS_CURRENCY,
    STARS_PRICE,
    TERMS_VERSION,
    StarsPaymentOrder,
    StarsReceipt,
    checkout_error,
    validate_stars_receipt,
)
from pin.contexts.billing.infrastructure.models import (
    AccessAccountRecord,
    AccessPeriodRecord,
    PaymentOrderRecord,
    PaymentReceiptRecord,
    PaymentRefundRecord,
    TermsAcceptanceRecord,
)
from pin.contexts.billing.infrastructure.store import SqlAlchemyAccessUsageStore


class PaymentRejected(InvalidTransition):
    """A provider update or user action cannot change the billing ledger."""


@dataclass(frozen=True, slots=True)
class PaymentInvoice:
    order_id: UUID
    owner_id: UUID
    telegram_user_id: int
    payload: str
    terms_version: str
    invoice_status: str = "queued"


@dataclass(frozen=True, slots=True)
class CheckoutDecision:
    ok: bool
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentCancellation:
    order_id: UUID
    owner_id: UUID
    telegram_user_id: int
    first_charge_id: str


def paid_period_id(charge_id: str) -> str:
    """Keep charge identity immutable while fitting the access ledger's 128-char key."""
    return f"stars-{uuid5(NAMESPACE_URL, charge_id)}"


def _require_aware(value: datetime, name: str = "at") -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _domain_order(row: PaymentOrderRecord) -> StarsPaymentOrder:
    return StarsPaymentOrder(
        id=row.id,
        owner_id=row.owner_id,
        telegram_user_id=row.telegram_user_id,
        payload=row.invoice_payload,
        terms_version=row.terms_version,
        first_charge_id=row.first_charge_id,
        renewal_cancelled_at=row.renewal_cancelled_at,
        latest_period_end=row.latest_period_end,
    )


class SqlAlchemyStarsPaymentStore:
    """All methods use the caller's transaction; provider calls belong outside it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def renewal_status(self, owner_id: UUID) -> str | None:
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(
                PaymentOrderRecord.owner_id == owner_id,
                PaymentOrderRecord.first_charge_id.is_not(None),
            )
            .order_by(PaymentOrderRecord.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        if row.renewal_cancelled_at is not None:
            return "cancelled"
        return row.cancel_status

    async def view_terms(self, command: ViewPaymentTerms) -> None:
        _require_aware(command.at)
        await self._session.execute(
            insert(TermsAcceptanceRecord)
            .values(
                owner_id=command.owner_id,
                terms_version=TERMS_VERSION,
                viewed_at=command.at,
            )
            .on_conflict_do_update(
                index_elements=[
                    TermsAcceptanceRecord.owner_id,
                    TermsAcceptanceRecord.terms_version,
                ],
                set_={"viewed_at": command.at},
            )
        )

    async def accept_terms(self, command: AcceptPaymentTerms) -> None:
        _require_aware(command.at)
        record = await self._session.scalar(
            select(TermsAcceptanceRecord)
            .where(
                TermsAcceptanceRecord.owner_id == command.owner_id,
                TermsAcceptanceRecord.terms_version == TERMS_VERSION,
            )
            .with_for_update()
        )
        if record is None:
            raise PaymentRejected("read /terms before accepting the subscription terms")
        record.accepted_at = command.at

    async def recover_uncertain_provider_calls(
        self,
        *,
        at: datetime,
        lease_for: timedelta = timedelta(minutes=2),
        limit: int = 100,
    ) -> int:
        """Fence provider calls left in flight after a worker stopped unexpectedly."""
        _require_aware(at)
        if lease_for <= timedelta(0) or limit < 1:
            raise ValueError("provider call recovery requires a positive lease and limit")
        cutoff = at.astimezone(UTC) - lease_for
        invoices = list(
            (
                await self._session.scalars(
                    select(PaymentOrderRecord)
                    .where(
                        PaymentOrderRecord.invoice_status == "sending",
                        PaymentOrderRecord.invoice_started_at <= cutoff,
                    )
                    .order_by(PaymentOrderRecord.invoice_started_at, PaymentOrderRecord.id)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).all()
        )
        cancellations = list(
            (
                await self._session.scalars(
                    select(PaymentOrderRecord)
                    .where(
                        PaymentOrderRecord.cancel_status == "sending",
                        PaymentOrderRecord.cancel_started_at <= cutoff,
                    )
                    .order_by(PaymentOrderRecord.cancel_started_at, PaymentOrderRecord.id)
                    .with_for_update(skip_locked=True)
                    .limit(max(0, limit - len(invoices)))
                )
            ).all()
        )
        for row in invoices:
            row.invoice_status = "unknown"
            row.invoice_error_code = "provider_outcome_unknown_after_worker_restart"
            row.invoice_started_at = None
        for row in cancellations:
            row.cancel_status = "unknown"
            row.cancel_error_code = "provider_outcome_unknown_after_worker_restart"
            row.cancel_started_at = None
        return len(invoices) + len(cancellations)

    async def create_order(self, command: CreateStarsOrder) -> PaymentInvoice:
        _require_aware(command.at)
        if type(command.telegram_user_id) is not int or command.telegram_user_id < 1:
            raise ValueError("telegram_user_id must be positive")
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        if account is None or account.deleted_at is not None:
            raise PaymentRejected("an active account is required to create a payment order")
        accepted = await self._session.scalar(
            select(TermsAcceptanceRecord).where(
                TermsAcceptanceRecord.owner_id == command.owner_id,
                TermsAcceptanceRecord.terms_version == TERMS_VERSION,
                TermsAcceptanceRecord.accepted_at.is_not(None),
            )
        )
        if accepted is None:
            raise PaymentRejected("current subscription terms have not been accepted")
        active_paid = await self._session.scalar(
            select(AccessPeriodRecord.period_id)
            .where(
                AccessPeriodRecord.owner_id == command.owner_id,
                AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                AccessPeriodRecord.ends_at > command.at,
            )
            .limit(1)
        )
        if active_paid is not None:
            raise PaymentRejected("paid access is active or scheduled")
        existing = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(
                PaymentOrderRecord.owner_id == command.owner_id,
                PaymentOrderRecord.state.in_(("open", "prechecked")),
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.invoice_status == "failed":
                existing.invoice_status = "queued"
                existing.invoice_error_code = None
            return PaymentInvoice(
                existing.id,
                existing.owner_id,
                existing.telegram_user_id,
                existing.invoice_payload,
                existing.terms_version,
                existing.invoice_status,
            )
        if not command.invoice_payload or len(command.invoice_payload.encode("utf-8")) > 128:
            raise ValueError("invoice payload must be between 1 and 128 bytes")
        row = PaymentOrderRecord(
            owner_id=command.owner_id,
            telegram_user_id=command.telegram_user_id,
            invoice_payload=command.invoice_payload,
            terms_version=TERMS_VERSION,
            state="open",
            created_at=command.at.astimezone(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return PaymentInvoice(
            row.id,
            row.owner_id,
            row.telegram_user_id,
            row.invoice_payload,
            row.terms_version,
            row.invoice_status,
        )

    async def claim_invoice(self, *, at: datetime) -> PaymentInvoice | None:
        """Claim a queued invoice before making the Telegram call in a later phase."""
        _require_aware(at)
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(
                PaymentOrderRecord.invoice_status == "queued",
                PaymentOrderRecord.state.in_(("open", "prechecked")),
            )
            .order_by(PaymentOrderRecord.created_at, PaymentOrderRecord.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.invoice_status = "sending"
        row.invoice_started_at = at.astimezone(UTC)
        return PaymentInvoice(
            row.id,
            row.owner_id,
            row.telegram_user_id,
            row.invoice_payload,
            row.terms_version,
            row.invoice_status,
        )

    async def finish_invoice(
        self,
        *,
        order_id: UUID,
        status: str,
        at: datetime,
        message_id: int | None = None,
        error_code: str | None = None,
    ) -> bool:
        _require_aware(at)
        if status not in {"sent", "failed", "unknown"}:
            raise ValueError("invoice outcome must be sent, failed, or unknown")
        row = await self._session.scalar(
            select(PaymentOrderRecord).where(PaymentOrderRecord.id == order_id).with_for_update()
        )
        if row is None or row.invoice_status != "sending":
            return False
        row.invoice_status = status
        row.invoice_started_at = None
        row.invoice_message_id = message_id if status == "sent" else None
        row.invoice_error_code = error_code if status != "sent" else None
        return True

    async def request_cancellation(self, *, owner_id: UUID, at: datetime) -> str:
        _require_aware(at)
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == owner_id)
            .with_for_update()
        )
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(
                PaymentOrderRecord.owner_id == owner_id,
                PaymentOrderRecord.first_charge_id.is_not(None),
            )
            .order_by(PaymentOrderRecord.created_at.desc())
            .with_for_update()
            .limit(1)
        )
        if account is None or account.deleted_at is not None or row is None:
            raise PaymentRejected("no cancellable subscription was found")
        if row.renewal_cancelled_at is not None:
            return "cancelled"
        if row.latest_period_end is None or row.latest_period_end <= at:
            raise PaymentRejected("the current subscription period has ended")
        if row.cancel_status in {"queued", "sending"}:
            return "pending"
        if row.cancel_status == "unknown":
            raise PaymentRejected("the prior cancellation attempt needs operator reconciliation")
        row.cancel_status = "queued"
        row.cancel_requested_at = at.astimezone(UTC)
        return "pending"

    async def claim_cancellation(self, *, at: datetime) -> PaymentCancellation | None:
        _require_aware(at)
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(PaymentOrderRecord.cancel_status == "queued")
            .order_by(PaymentOrderRecord.cancel_requested_at, PaymentOrderRecord.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        if (
            row.first_charge_id is None
            or row.latest_period_end is None
            or row.latest_period_end <= at
        ):
            row.cancel_status = "failed"
            row.cancel_error_code = "period_expired_before_cancel"
            return None
        row.cancel_status = "sending"
        row.cancel_started_at = at.astimezone(UTC)
        return PaymentCancellation(row.id, row.owner_id, row.telegram_user_id, row.first_charge_id)

    async def finish_cancellation(
        self, *, order_id: UUID, status: str, at: datetime, error_code: str | None = None
    ) -> bool:
        _require_aware(at)
        if status not in {"confirmed", "failed", "unknown"}:
            raise ValueError("cancellation outcome must be confirmed, failed, or unknown")
        row = await self._session.scalar(
            select(PaymentOrderRecord).where(PaymentOrderRecord.id == order_id).with_for_update()
        )
        if row is None or row.cancel_status != "sending":
            return False
        row.cancel_status = status
        row.cancel_started_at = None
        row.cancel_error_code = error_code if status != "confirmed" else None
        if status == "confirmed":
            row.renewal_cancelled_at = at.astimezone(UTC)
            row.state = "cancelled"
        return True

    async def check_checkout(self, command: CheckStarsCheckout) -> CheckoutDecision:
        _require_aware(command.at)
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(PaymentOrderRecord.invoice_payload == command.invoice_payload)
            .with_for_update()
        )
        if row is None:
            return CheckoutDecision(
                False, "This payment order is invalid. Open /subscription to start again."
            )
        if row.pre_checkout_query_id == command.query_id:
            same_request = bool(
                account is not None
                and account.deleted_at is None
                and row.owner_id == command.owner_id
                and row.telegram_user_id == command.telegram_user_id
                and command.currency == STARS_CURRENCY
                and command.total_amount == STARS_PRICE
            )
            return CheckoutDecision(
                same_request,
                None
                if same_request
                else "This payment order is invalid. Open /subscription to start again.",
            )
        active_paid = False
        if account is not None:
            active_paid = (
                await self._session.scalar(
                    select(AccessPeriodRecord.period_id)
                    .where(
                        AccessPeriodRecord.owner_id == command.owner_id,
                        AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                        AccessPeriodRecord.ends_at > command.at,
                    )
                    .limit(1)
                )
            ) is not None
        accepted = await self._session.scalar(
            select(TermsAcceptanceRecord.accepted_at).where(
                TermsAcceptanceRecord.owner_id == row.owner_id,
                TermsAcceptanceRecord.terms_version == row.terms_version,
            )
        )
        error = checkout_error(
            _domain_order(row),
            telegram_user_id=command.telegram_user_id,
            currency=command.currency,
            total_amount=command.total_amount,
            terms_accepted=accepted is not None,
            active_paid=active_paid,
            already_prechecked=row.state == "prechecked",
        )
        if (
            error is not None
            or account is None
            or account.deleted_at is not None
            or row.owner_id != command.owner_id
        ):
            return CheckoutDecision(False, error or "Payment is unavailable for this account.")
        row.state = "prechecked"
        row.pre_checkout_query_id = command.query_id
        return CheckoutDecision(True)

    async def record_payment(self, command: RecordStarsPayment) -> bool:
        _require_aware(command.at)
        account = await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == command.owner_id)
            .with_for_update()
        )
        row = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(PaymentOrderRecord.invoice_payload == command.invoice_payload)
            .with_for_update()
        )
        if (
            row is None
            or account is None
            or account.deleted_at is not None
            or row.owner_id != command.owner_id
            or row.telegram_user_id != command.telegram_user_id
        ):
            raise PaymentRejected("confirmed payment has no matching active order")
        prior = await self._session.scalar(
            select(PaymentReceiptRecord)
            .where(PaymentReceiptRecord.telegram_charge_id == command.telegram_charge_id)
            .with_for_update()
        )
        if prior is not None:
            if (
                prior.owner_id != command.owner_id
                or prior.telegram_user_id != command.telegram_user_id
                or prior.order_id != row.id
            ):
                raise PaymentRejected(
                    "Telegram charge identifier conflicts with an existing receipt"
                )
            return True
        receipt = StarsReceipt(
            owner_id=command.owner_id,
            telegram_user_id=command.telegram_user_id,
            payload=command.invoice_payload,
            charge_id=command.telegram_charge_id,
            provider_charge_id=command.provider_charge_id,
            currency=command.currency,
            total_amount=command.total_amount,
            is_recurring=command.is_recurring,
            is_first_recurring=command.is_first_recurring,
            subscription_expiration_date=command.subscription_expiration_date,
            received_at=command.at,
        )
        try:
            starts_at, ends_at = validate_stars_receipt(_domain_order(row), receipt)
        except ValueError as error:
            raise PaymentRejected(str(error)) from error
        if row.first_charge_id is None and row.state != "prechecked":
            raise PaymentRejected("initial Stars receipt did not follow an approved checkout")
        if command.provider_charge_id:
            provider_prior = await self._session.scalar(
                select(PaymentReceiptRecord.telegram_charge_id).where(
                    PaymentReceiptRecord.provider_charge_id == command.provider_charge_id
                )
            )
            if provider_prior is not None:
                raise PaymentRejected("provider charge identifier was already recorded")
        await SqlAlchemyAccessUsageStore(self._session).activate_paid_period(
            ActivatePaidPeriod(
                command.owner_id,
                paid_period_id(command.telegram_charge_id),
                starts_at,
                ends_at,
            )
        )
        self._session.add(
            PaymentReceiptRecord(
                telegram_charge_id=command.telegram_charge_id,
                provider_charge_id=command.provider_charge_id,
                order_id=row.id,
                owner_id=command.owner_id,
                telegram_user_id=command.telegram_user_id,
                currency=command.currency,
                total_amount=command.total_amount,
                is_recurring=command.is_recurring,
                is_first_recurring=command.is_first_recurring,
                period_starts_at=starts_at,
                period_ends_at=ends_at,
                received_at=command.at.astimezone(UTC),
            )
        )
        if row.first_charge_id is None:
            row.first_charge_id = command.telegram_charge_id
        row.latest_period_end = ends_at
        row.state = "cancelled" if row.renewal_cancelled_at is not None else "paid"
        return True

    async def begin_refund(self, command: RequestStarsRefund) -> tuple[int, str]:
        """Create one explicit operator request; callers make the Bot API call after commit."""
        _require_aware(command.at)
        operator = command.requested_by.strip()
        if not operator or len(operator) > 128:
            raise ValueError("refund operator identity must contain 1 to 128 characters")
        receipt = await self._session.get(PaymentReceiptRecord, command.telegram_charge_id)
        if receipt is None or receipt.refunded_at is not None:
            raise PaymentRejected("receipt does not exist or is already refunded")
        await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == receipt.owner_id)
            .with_for_update()
        )
        order = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(PaymentOrderRecord.id == receipt.order_id)
            .with_for_update()
        )
        receipt = await self._session.scalar(
            select(PaymentReceiptRecord)
            .where(PaymentReceiptRecord.telegram_charge_id == command.telegram_charge_id)
            .with_for_update()
        )
        if receipt is None or receipt.refunded_at is not None:
            raise PaymentRejected("receipt does not exist or is already refunded")
        existing = await self._session.scalar(
            select(PaymentRefundRecord)
            .where(PaymentRefundRecord.telegram_charge_id == command.telegram_charge_id)
            .with_for_update()
        )
        if existing is not None:
            raise PaymentRejected("a refund attempt already exists; reconcile its recorded status")
        user_id = receipt.telegram_user_id
        if order is None or order.first_charge_id is None:
            raise PaymentRejected("receipt has no recoverable subscription")
        self._session.add(
            PaymentRefundRecord(
                telegram_charge_id=command.telegram_charge_id,
                requested_by=operator,
                status="requested",
                requested_at=command.at.astimezone(UTC),
            )
        )
        return user_id, command.telegram_charge_id

    async def finish_refund(self, command: FinishStarsRefund) -> bool:
        _require_aware(command.at)
        if command.status not in {"confirmed", "failed", "unknown"}:
            raise ValueError("refund status must be confirmed, failed, or unknown")
        request_exists = await self._session.get(PaymentRefundRecord, command.telegram_charge_id)
        receipt = await self._session.get(PaymentReceiptRecord, command.telegram_charge_id)
        if request_exists is None or receipt is None:
            return False
        await self._session.scalar(
            select(AccessAccountRecord)
            .where(AccessAccountRecord.owner_id == receipt.owner_id)
            .with_for_update()
        )
        order = await self._session.scalar(
            select(PaymentOrderRecord)
            .where(PaymentOrderRecord.id == receipt.order_id)
            .with_for_update()
        )
        receipt = await self._session.scalar(
            select(PaymentReceiptRecord)
            .where(PaymentReceiptRecord.telegram_charge_id == command.telegram_charge_id)
            .with_for_update()
        )
        request = await self._session.scalar(
            select(PaymentRefundRecord)
            .where(PaymentRefundRecord.telegram_charge_id == command.telegram_charge_id)
            .with_for_update()
        )
        if request is None or request.status != "requested":
            return False
        if receipt is None or order is None:
            raise PaymentRejected("refund request lost its receipt")
        request.status = command.status
        request.finished_at = command.at.astimezone(UTC)
        if command.status == "confirmed":
            await self._session.execute(
                delete(AccessPeriodRecord).where(
                    AccessPeriodRecord.owner_id == receipt.owner_id,
                    AccessPeriodRecord.period_id == paid_period_id(receipt.telegram_charge_id),
                    AccessPeriodRecord.policy_kind == PeriodKind.PAID.value,
                )
            )
            receipt.refunded_at = command.at.astimezone(UTC)
        return True
