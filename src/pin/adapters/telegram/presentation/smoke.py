"""Opt-in, single-chat Telegram presentation smoke test."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from pin.adapters.telegram.presentation.delivery import send_formatted_message, typing_status
from pin.adapters.telegram.presentation.formatting import (
    MessageEntry,
    MessagePage,
    paginate_entries,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class SmokeSettings:
    bot_token: str
    test_chat_id: int


def _parse_local_config(content: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines without interpolation or exposing values."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or key not in {"PIN_BOT_TOKEN", "PIN_TEST_CHAT_ID"}:
            continue
        if value.startswith(('"', "'")) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        if key in values:
            raise ValueError(f"duplicate local setting on line {line_number}")
        values[key] = value
    return values


def load_settings(
    *,
    project_root: Path | None = None,
    environ: dict[str, str] | None = None,
) -> SmokeSettings:
    """Load the two allowed settings from the process environment or ignored .env.local."""
    source_env = os.environ if environ is None else environ
    local_values: dict[str, str] = {}
    config_path = (project_root or Path.cwd()) / ".env.local"
    if config_path.is_file():
        local_values = _parse_local_config(config_path.read_text(encoding="utf-8"))

    token = source_env.get("PIN_BOT_TOKEN") or local_values.get("PIN_BOT_TOKEN")
    chat_value = source_env.get("PIN_TEST_CHAT_ID") or local_values.get("PIN_TEST_CHAT_ID")
    if not token or not chat_value:
        raise ValueError("configure PIN_BOT_TOKEN and PIN_TEST_CHAT_ID in .env.local")
    if any(character.isspace() for character in token):
        raise ValueError("PIN_BOT_TOKEN has an invalid format")
    try:
        chat_id = int(chat_value)
    except ValueError as error:
        raise ValueError("PIN_TEST_CHAT_ID must be a private-chat numeric ID") from error
    if chat_id == 0:
        raise ValueError("PIN_TEST_CHAT_ID must be a non-zero private-chat numeric ID")
    return SmokeSettings(token, chat_id)


def build_smoke_pages(language: str) -> list[MessagePage]:
    """Build deliberately long RU/EN examples with every MarkdownV2 special character."""
    if language == "ru":
        title = "Проверка Telegram форматирования"
        sample = "Спецсимволы _*[]()~`>#+-=|{}.!\\; Unicode: Пример 😀 ё. "
        empty_text = "Нет примеров"
    elif language == "en":
        title = "Telegram formatting check"
        sample = "Reserved chars _*[]()~`>#+-=|{}.!\\; Unicode: Example 😀 é. "
        empty_text = "No examples"
    else:
        raise ValueError("language must be 'ru' or 'en'")
    entry = MessageEntry(sample * 100)
    pages = paginate_entries(title, [entry], empty_text=empty_text)
    if len(pages) < 2:
        raise AssertionError("smoke example should exercise pagination")
    return pages


def build_smoke_keyboard(language: str) -> ReplyKeyboardMarkup:
    label = "Тест завершён" if language == "ru" else "Smoke test complete"
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def validate_private_chat(chat: object, configured_chat_id: int) -> None:
    if getattr(chat, "id", None) != configured_chat_id or getattr(chat, "type", None) != "private":
        raise ValueError("configured test chat did not validate as the exact private chat")


def _entity_summary(message: Message) -> str:
    counts = Counter(str(entity.type) for entity in (message.entities or []))
    return ",".join(f"{name}:{count}" for name, count in sorted(counts.items())) or "none"


async def run_smoke(bot: Bot, settings: SmokeSettings) -> list[str]:
    """Validate one private chat, then send only the two fixed localized examples there."""
    chat = await bot.get_chat(settings.test_chat_id)
    validate_private_chat(chat, settings.test_chat_id)

    statuses: list[str] = []
    for language in ("ru", "en"):
        pages = build_smoke_pages(language)
        async with typing_status(bot, settings.test_chat_id):
            for page_number, page in enumerate(pages, start=1):
                markup = build_smoke_keyboard(language) if page_number == len(pages) else None
                message = await send_formatted_message(
                    bot,
                    chat_id=settings.test_chat_id,
                    markdown=page.markdown,
                    plain_text=page.plain_text,
                    reply_markup=markup,
                )
                statuses.append(
                    f"{language} page={page_number}/{len(pages)} "
                    f"message_id={message.message_id} entities={_entity_summary(message)}"
                )
    return statuses


def main(
    argv: list[str] | None = None, *, settings_loader: Callable[[], SmokeSettings] = load_settings
) -> int:
    parser = argparse.ArgumentParser(
        description="Send a fixed RU/EN Telegram presentation smoke test"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="send to the configured private test chat; default validates examples locally",
    )
    args = parser.parse_args(argv)
    if not args.send:
        for language in ("ru", "en"):
            pages = build_smoke_pages(language)
            print(f"{language}: {len(pages)} pages prepared; no Telegram API calls made")
        print(
            "Use --send only after configuring the dedicated private test chat and opening /start."
        )
        return 0

    try:
        settings = settings_loader()
        asyncio.run(_run_with_bot(settings))
    except Exception as error:
        # Never echo parser or network exceptions: they may contain request details.
        print(
            f"Smoke test stopped: {type(error).__name__}; "
            "check local settings and private chat setup."
        )
        return 2
    return 0


async def _run_with_bot(settings: SmokeSettings) -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )
    try:
        statuses = await run_smoke(bot, settings)
        for status in statuses:
            print(status)
        print("Telegram API accepted the messages; verify their appearance in the test client.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
