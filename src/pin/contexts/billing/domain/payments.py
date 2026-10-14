"""Rules for Telegram Stars recurring payments; provider facts remain explicit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

STARS_CURRENCY = "XTR"
STARS_PRICE = 250
STARS_PERIOD = timedelta(days=30)
STARS_PERIOD_SECONDS = 2_592_000
TERMS_VERSION = "stars-monthly-v1"


@dataclass(frozen=True, slots=True)
class StarsPaymentOrder:
    id: UUID
    owner_id: UUID
    telegram_user_id: int
    payload: str
    terms_version: str
    first_charge_id: str | None = None
    renewal_cancelled_at: datetime | None = None
    latest_period_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class StarsReceipt:
    owner_id: UUID
    telegram_user_id: int
    payload: str
    charge_id: str
    provider_charge_id: str | None
    currency: str
    total_amount: int
    is_recurring: bool
    is_first_recurring: bool
    subscription_expiration_date: datetime | None
    received_at: datetime


def validate_stars_receipt(
    order: StarsPaymentOrder, receipt: StarsReceipt
) -> tuple[datetime, datetime]:
    """Validate the authenticated receipt facts and return its provider-defined window."""
    for value, name in ((receipt.received_at, "received_at"),):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if (
        receipt.owner_id != order.owner_id
        or type(receipt.telegram_user_id) is not int
        or receipt.telegram_user_id != order.telegram_user_id
        or receipt.payload != order.payload
    ):
        raise ValueError("payment receipt does not belong to its order")
    if receipt.currency != STARS_CURRENCY or receipt.total_amount != STARS_PRICE:
        raise ValueError("payment receipt amount or currency is invalid")
    if not receipt.charge_id or len(receipt.charge_id) > 128:
        raise ValueError("Telegram payment charge ID is invalid")
    if not receipt.is_recurring or receipt.subscription_expiration_date is None:
        raise ValueError("payment receipt does not confirm a recurring period")
    end = receipt.subscription_expiration_date
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("subscription expiration must be timezone-aware")
    start = end - STARS_PERIOD
    if receipt.is_first_recurring != (order.first_charge_id is None):
        raise ValueError("recurring payment order state does not match receipt")
    if order.latest_period_end is not None and end <= order.latest_period_end:
        raise ValueError("recurring payment period is duplicate or out of order")
    if order.latest_period_end is not None and start < order.latest_period_end:
        raise ValueError("recurring payment period overlaps prior coverage")
    return start.astimezone(UTC), end.astimezone(UTC)


def checkout_error(
    order: StarsPaymentOrder | None,
    *,
    telegram_user_id: int,
    currency: str,
    total_amount: int,
    terms_accepted: bool,
    active_paid: bool,
    already_prechecked: bool,
) -> str | None:
    """Return a user-safe denial reason; checkout approval never grants access."""
    if order is None or order.telegram_user_id != telegram_user_id:
        return "This payment order is invalid. Open /subscription to start again."
    if order.first_charge_id is not None or already_prechecked:
        return "This payment order has already been used. Open /subscription to continue."
    if currency != STARS_CURRENCY or total_amount != STARS_PRICE:
        return "The payment amount is invalid. No access was granted."
    if not terms_accepted or order.terms_version != TERMS_VERSION:
        return "Please read and accept the current terms before paying: /terms."
    if active_paid:
        return "A paid period is active or scheduled. Use /subscription to check its end date."
    return None
