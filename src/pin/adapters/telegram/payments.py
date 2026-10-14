"""Persist-first Telegram payment update handling."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from logging import getLogger
from math import floor
from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pin.adapters.telegram.ingress import PostgresTelegramOwnerResolver
from pin.contexts.billing.api import CheckStarsCheckout, SqlAlchemyStarsPaymentStore

logger = getLogger("pin.telegram.payments")
PRECHECKOUT_DEADLINE_SECONDS = 10.0
PRECHECKOUT_ANSWER_RESERVE_SECONDS = 2.0


class PreCheckoutOutcomeUnknown(RuntimeError):
    """The payment query was persisted but Telegram's answer outcome is unknown."""


def pre_checkout_query(update: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = update.get("pre_checkout_query")
    return value if isinstance(value, Mapping) else None


async def answer_pre_checkout(
    sessions: async_sessionmaker[AsyncSession],
    bot: Bot,
    *,
    bot_id: int,
    update: Mapping[str, Any],
    now: datetime,
    received_at: float | None = None,
) -> bool | None:
    """Commit Billing's decision and reserve time for Telegram's ten-second deadline."""
    query = pre_checkout_query(update)
    if query is None:
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("pre-checkout clock must be timezone-aware")
    loop = asyncio.get_running_loop()
    deadline = (
        (received_at if received_at is not None else loop.time())
        + PRECHECKOUT_DEADLINE_SECONDS
    )
    sender = query.get("from")
    user_id = sender.get("id") if isinstance(sender, Mapping) else None
    query_id = query.get("id")
    payload = query.get("invoice_payload")
    currency = query.get("currency")
    amount = query.get("total_amount")
    error_message: str | None
    if (
        type(user_id) is not int
        or user_id < 1
        or not isinstance(query_id, str)
        or not query_id
        or len(query_id) > 128
        or not isinstance(payload, str)
        or not payload
        or not isinstance(currency, str)
        or type(amount) is not int
    ):
        decision_ok = False
        error_message = "This payment order is invalid. Open /subscription to start again."
    else:
        decision_budget = deadline - loop.time() - PRECHECKOUT_ANSWER_RESERVE_SECONDS
        if decision_budget <= 0:
            decision_ok = False
            error_message = "Checkout timed out. Open /subscription to start again."
        else:
            try:
                async with asyncio.timeout(decision_budget):
                    async with sessions() as session, session.begin():
                        owner_id: UUID = await PostgresTelegramOwnerResolver().resolve(
                            session, bot_id=bot_id, telegram_user_id=user_id
                        )
                        decision = await SqlAlchemyStarsPaymentStore(session).check_checkout(
                            CheckStarsCheckout(
                                owner_id=owner_id,
                                telegram_user_id=user_id,
                                invoice_payload=payload,
                                query_id=query_id,
                                currency=currency,
                                total_amount=amount,
                                at=now.astimezone(UTC),
                            )
                        )
                decision_ok = decision.ok
                error_message = decision.error_message or "This payment order is invalid."
            except (TimeoutError, SQLAlchemyError) as error:
                logger.warning(
                    "telegram_pre_checkout_validation_unavailable error=%s",
                    type(error).__name__,
                )
                decision_ok = False
                error_message = (
                    "Checkout could not be verified in time. Open /subscription to start again."
                )
    remaining = deadline - loop.time()
    if remaining <= 0:
        logger.warning("telegram_pre_checkout_answer_deadline_exhausted")
        raise PreCheckoutOutcomeUnknown
    request_timeout = max(1, floor(remaining))
    try:
        async with asyncio.timeout(remaining):
            response = await bot.answer_pre_checkout_query(
                pre_checkout_query_id=str(query.get("id", "")),
                ok=decision_ok,
                error_message=error_message if not decision_ok else None,
                request_timeout=request_timeout,
            )
    except TelegramBadRequest:
        logger.info("telegram_pre_checkout_answer_rejected")
        return False
    except Exception as error:
        logger.warning("telegram_pre_checkout_answer_unknown error=%s", type(error).__name__)
        raise PreCheckoutOutcomeUnknown from None
    return bool(response)
