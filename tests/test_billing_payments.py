from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pin.contexts.billing.domain.payments import (
    STARS_CURRENCY,
    STARS_PERIOD,
    STARS_PRICE,
    TERMS_VERSION,
    StarsPaymentOrder,
    StarsReceipt,
    checkout_error,
    validate_stars_receipt,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def order(**overrides: object) -> StarsPaymentOrder:
    fields = {
        "id": uuid4(),
        "owner_id": uuid4(),
        "telegram_user_id": 700_000_001,
        "payload": "invoice-payload",
        "terms_version": TERMS_VERSION,
    }
    fields.update(overrides)
    return StarsPaymentOrder(**fields)


def receipt(payment_order: StarsPaymentOrder, **overrides: object) -> StarsReceipt:
    fields = {
        "owner_id": payment_order.owner_id,
        "telegram_user_id": payment_order.telegram_user_id,
        "payload": payment_order.payload,
        "charge_id": "charge-1",
        "provider_charge_id": "provider-1",
        "currency": STARS_CURRENCY,
        "total_amount": STARS_PRICE,
        "is_recurring": True,
        "is_first_recurring": True,
        "subscription_expiration_date": NOW + STARS_PERIOD,
        "received_at": NOW,
    }
    fields.update(overrides)
    return StarsReceipt(**fields)


def test_authenticated_receipt_defines_exact_paid_window() -> None:
    payment_order = order()

    starts_at, ends_at = validate_stars_receipt(payment_order, receipt(payment_order))

    assert starts_at == NOW
    assert ends_at == NOW + timedelta(days=30)


@pytest.mark.parametrize(
    "facts",
    [
        {"owner_id": uuid4()},
        {"telegram_user_id": 700_000_002},
        {"payload": "different-order"},
        {"currency": "USD"},
        {"total_amount": STARS_PRICE - 1},
        {"is_recurring": False},
        {"is_first_recurring": False},
        {"subscription_expiration_date": None},
        {"subscription_expiration_date": datetime(2026, 11, 13)},
        {"charge_id": ""},
        {"received_at": datetime(2026, 10, 14, 12)},
    ],
)
def test_malformed_or_mismatched_provider_receipt_is_rejected(facts: dict[str, object]) -> None:
    payment_order = order()
    with pytest.raises(ValueError):
        validate_stars_receipt(payment_order, receipt(payment_order, **facts))


def test_renewal_must_be_the_next_nonoverlapping_provider_period() -> None:
    prior_end = NOW + STARS_PERIOD
    payment_order = order(first_charge_id="charge-1", latest_period_end=prior_end)
    next_end = prior_end + STARS_PERIOD
    starts_at, ends_at = validate_stars_receipt(
        payment_order,
        receipt(
            payment_order,
            charge_id="charge-2",
            is_first_recurring=False,
            subscription_expiration_date=next_end,
        ),
    )
    assert (starts_at, ends_at) == (prior_end, next_end)

    overlapping = replace(
        receipt(
            payment_order,
            charge_id="charge-3",
            is_first_recurring=False,
            subscription_expiration_date=prior_end + timedelta(days=15),
        ),
    )
    with pytest.raises(ValueError, match="overlaps"):
        validate_stars_receipt(payment_order, overlapping)

    out_of_order = replace(
        receipt(
            payment_order,
            charge_id="charge-4",
            is_first_recurring=False,
            subscription_expiration_date=prior_end,
        ),
    )
    with pytest.raises(ValueError, match="duplicate or out of order"):
        validate_stars_receipt(payment_order, out_of_order)


def test_precheckout_requires_exact_stars_terms_and_no_active_paid_period() -> None:
    payment_order = order()
    facts = {
        "telegram_user_id": payment_order.telegram_user_id,
        "currency": STARS_CURRENCY,
        "total_amount": STARS_PRICE,
        "terms_accepted": True,
        "active_paid": False,
        "already_prechecked": False,
    }
    assert checkout_error(payment_order, **facts) is None
    assert checkout_error(payment_order, **{**facts, "total_amount": 249})
    assert checkout_error(payment_order, **{**facts, "terms_accepted": False})
    assert checkout_error(payment_order, **{**facts, "active_paid": True})
    assert checkout_error(payment_order, **{**facts, "already_prechecked": True})
