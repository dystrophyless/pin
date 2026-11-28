from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest, TelegramNotFound

from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.adapters.telegram.widget_api import SendWidget
from pin.contexts.notifications.application.delivery import TelegramReplySend


class _FakeBot:
    id = 17

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1)

    async def __call__(self, method: object, **_: object) -> SimpleNamespace:
        self.sent.append({"method": method})
        return SimpleNamespace(message_id=2)


def test_plain_reply_transport_does_not_parse_recovery_titles_as_markdown() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]
        plain = "These items remain unfinished:\n• Keep *asterisks* literally"

        await transport.send_reply(
            TelegramReplySend(
                destination_id=456,
                markdown=plain,
                plain_text=plain,
                parse_mode=None,
            )
        )

        assert bot.sent == [
            {
                "chat_id": 456,
                "text": plain,
                "parse_mode": None,
                "reply_markup": None,
            }
        ]

    asyncio.run(scenario())


def test_widget_reply_uses_send_widget_with_plain_caption_and_queries_enabled() -> None:
    async def scenario() -> None:
        bot = _FakeBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]
        await transport.send_reply(
            TelegramReplySend(
                destination_id=456,
                markdown="Choose a time zone",
                plain_text="Choose a time zone",
                parse_mode=None,
                widget_html="<html><body>timezone chooser</body></html>",
            )
        )

        assert len(bot.sent) == 1
        method = bot.sent[0]["method"]
        assert isinstance(method, SendWidget)
        assert method.chat_id == 456
        assert method.caption == "Choose a time zone"
        assert method.allow_queries is True

    asyncio.run(scenario())


def test_widget_bad_request_falls_back_to_plain_caption() -> None:
    class UnsupportedWidgetBot(_FakeBot):
        async def __call__(self, method: object, **_: object) -> SimpleNamespace:
            raise TelegramBadRequest(method=method, message="method not found")  # type: ignore[arg-type]

    async def scenario() -> None:
        bot = UnsupportedWidgetBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]
        await transport.send_reply(
            TelegramReplySend(
                destination_id=456,
                markdown="Choose a time zone",
                plain_text="Choose a time zone",
                parse_mode=None,
                widget_html="<html><body>timezone chooser</body></html>",
            )
        )

        assert bot.sent == [
            {
                "chat_id": 456,
                "text": "Choose a time zone",
                "parse_mode": None,
                "reply_markup": None,
            }
        ]

    asyncio.run(scenario())


def test_missing_send_widget_method_falls_back_to_plain_caption() -> None:
    class MissingWidgetMethodBot(_FakeBot):
        async def __call__(self, method: object, **_: object) -> SimpleNamespace:
            raise TelegramNotFound(method=method, message="Not Found")  # type: ignore[arg-type]

    async def scenario() -> None:
        bot = MissingWidgetMethodBot()
        transport = TelegramReminderTransport(bot)  # type: ignore[arg-type]
        await transport.send_reply(
            TelegramReplySend(
                destination_id=456,
                markdown="Choose a time zone",
                plain_text="Choose a time zone",
                parse_mode=None,
                widget_html="<html><body>timezone chooser</body></html>",
            )
        )

        assert bot.sent == [
            {
                "chat_id": 456,
                "text": "Choose a time zone",
                "parse_mode": None,
                "reply_markup": None,
            }
        ]

    asyncio.run(scenario())
