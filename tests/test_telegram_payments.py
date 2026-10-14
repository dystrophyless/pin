from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aiogram.types import LabeledPrice

from pin.adapters.telegram.payments import answer_pre_checkout
from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.contexts.billing.domain.payments import STARS_CURRENCY, STARS_PERIOD_SECONDS, STARS_PRICE
from pin.contexts.notifications.application.delivery import UnknownDeliveryFailure


class _FakeBot:
    id = 19

    def __init__(self) -> None:
        self.invoice_args: dict[str, object] | None = None
        self.sent_args: dict[str, object] | None = None
        self.cancel_args: dict[str, Any] | None = None
        self.precheckout_args: dict[str, Any] | None = None
        self.fail_invoice = False

    async def create_invoice_link(
        self,
        *,
        title: str,
        description: str,
        payload: str,
        provider_token: str,
        currency: str,
        prices: list[LabeledPrice],
        subscription_period: int,
    ) -> str:
        self.invoice_args = {
            "title": title,
            "description": description,
            "payload": payload,
            "provider_token": provider_token,
            "currency": currency,
            "prices": prices,
            "subscription_period": subscription_period,
        }
        if self.fail_invoice:
            raise OSError("injected transport failure")
        return "https://t.me/$test_invoice_link"

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: object,
    ) -> SimpleNamespace:
        self.sent_args = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
        }
        return SimpleNamespace(message_id=123)

    async def edit_user_star_subscription(
        self,
        *,
        user_id: int,
        telegram_payment_charge_id: str,
        is_canceled: bool,
    ) -> bool:
        self.cancel_args = {
            "user_id": user_id,
            "telegram_payment_charge_id": telegram_payment_charge_id,
            "is_canceled": is_canceled,
        }
        return True

    async def answer_pre_checkout_query(
        self,
        *,
        pre_checkout_query_id: str,
        ok: bool,
        error_message: str | None,
        request_timeout: int,
    ) -> bool:
        self.precheckout_args = {
            "pre_checkout_query_id": pre_checkout_query_id,
            "ok": ok,
            "error_message": error_message,
            "request_timeout": request_timeout,
        }
        return True


def test_invoice_adapter_creates_recurring_stars_link_and_sends_pay_button() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]

        message_id = await transport.send_stars_invoice(
            chat_id=751_000_001,
            payload="order-payload",
            language="en",
        )

        assert message_id == 123
        assert bot.invoice_args is not None
        assert bot.invoice_args["payload"] == "order-payload"
        assert bot.invoice_args["provider_token"] == ""
        assert bot.invoice_args["currency"] == STARS_CURRENCY
        assert bot.invoice_args["subscription_period"] == STARS_PERIOD_SECONDS
        prices = bot.invoice_args["prices"]
        assert len(prices) == 1
        assert prices[0].amount == STARS_PRICE
        assert bot.sent_args is not None
        assert bot.sent_args["chat_id"] == 751_000_001
        markup = bot.sent_args["reply_markup"]
        assert markup.inline_keyboard[0][0].url == "https://t.me/$test_invoice_link"

    asyncio.run(scenario())


def test_cancel_adapter_targets_initial_telegram_charge() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]

        assert await transport.cancel_stars_renewal(
            user_id=752_000_001,
            first_charge_id="initial-charge-id",
        )
        assert bot.cancel_args == {
            "user_id": 752_000_001,
            "telegram_payment_charge_id": "initial-charge-id",
            "is_canceled": True,
        }

    asyncio.run(scenario())


def test_unknown_invoice_send_is_not_reported_as_a_known_failure() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        bot.fail_invoice = True
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]

        try:
            await transport.send_stars_invoice(
                chat_id=753_000_001,
                payload="order-payload",
                language="en",
            )
        except UnknownDeliveryFailure as error:
            assert error.code == "telegram_invoice_outcome_unknown"
        else:
            raise AssertionError("transport uncertainty must remain distinguishable")

    asyncio.run(scenario())


def test_malformed_precheckout_is_denied_within_provider_deadline() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        result = await answer_pre_checkout(
            None,  # type: ignore[arg-type]
            bot,  # type: ignore[arg-type]
            bot_id=bot.id,
            update={
                "pre_checkout_query": {
                    "id": "query-1",
                    "from": {"id": 754_000_001},
                    "invoice_payload": "",
                    "currency": "XTR",
                    "total_amount": 250,
                }
            },
            now=datetime(2026, 10, 14, 12, tzinfo=UTC),
        )

        assert result
        assert bot.precheckout_args is not None
        assert bot.precheckout_args["ok"] is False
        assert 0 < bot.precheckout_args["request_timeout"] <= 10
        assert bot.precheckout_args["error_message"]

    asyncio.run(scenario())


def test_nearly_expired_precheckout_skips_database_and_is_denied() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        loop = asyncio.get_running_loop()
        result = await answer_pre_checkout(
            None,  # type: ignore[arg-type]
            bot,  # type: ignore[arg-type]
            bot_id=bot.id,
            update={
                "pre_checkout_query": {
                    "id": "query-nearly-expired",
                    "from": {"id": 754_000_002},
                    "invoice_payload": "valid-payload",
                    "currency": "XTR",
                    "total_amount": 250,
                }
            },
            now=datetime(2026, 10, 14, 12, tzinfo=UTC),
            received_at=loop.time() - 9.6,
        )

        assert result
        assert bot.precheckout_args is not None
        assert bot.precheckout_args["ok"] is False
        assert bot.precheckout_args["error_message"]

    asyncio.run(scenario())
