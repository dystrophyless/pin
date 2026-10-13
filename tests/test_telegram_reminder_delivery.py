from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pin.adapters.telegram.reminder_delivery import TelegramReminderTransport
from pin.contexts.notifications.application.delivery import TelegramReplySend


class _FakeBot:
    id = 17

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> SimpleNamespace:
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1)


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
