"""MarkdownV2 escaping and Unicode-safe message pagination."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

TELEGRAM_MESSAGE_LIMIT = 4096
_MARKDOWN_V2_SPECIALS = frozenset(r"_*[]()~`>#+-=|{}.!\\")


def telegram_utf16_length(value: str) -> int:
    """Count Telegram text/entity units without splitting non-BMP code points."""
    return len(value.encode("utf-16-le")) // 2


def escape_markdown_v2(value: str) -> str:
    """Escape untrusted plain text for a MarkdownV2 text context."""
    return "".join(f"\\{char}" if char in _MARKDOWN_V2_SPECIALS else char for char in value)


def _split_plain_text(value: str, limit: int) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if not value:
        return [""]

    chunks: list[str] = []
    start = 0
    while start < len(value):
        cursor = start
        used = 0
        preferred: int | None = None
        while cursor < len(value):
            units = 2 if ord(value[cursor]) > 0xFFFF else 1
            if used + units > limit:
                break
            used += units
            cursor += 1
            if value[cursor - 1] in "\n ":
                preferred = cursor
        if cursor == len(value):
            chunks.append(value[start:])
            break
        boundary = (
            preferred
            if preferred is not None and preferred - start >= limit // 2
            else cursor
        )
        if boundary == start:
            raise ValueError("limit cannot fit one Unicode code point")
        chunks.append(value[start:boundary])
        start = boundary
    return chunks


@dataclass(frozen=True, slots=True)
class MessageEntry:
    text: str
    target_id: str | None = None


@dataclass(frozen=True, slots=True)
class MessagePage:
    markdown: str
    plain_text: str
    target_ids: tuple[str, ...]


def paginate_entries(
    title: str,
    entries: Sequence[MessageEntry],
    *,
    empty_text: str,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> list[MessagePage]:
    """Render independent pages; each page has complete Markdown and its own button targets."""
    if limit < 8:
        raise ValueError("limit is too small for a page header")

    header_plain = title
    header_markdown = f"*{escape_markdown_v2(title)}*"
    header_units = telegram_utf16_length(header_plain)
    available = limit - header_units - 1
    if available < 1:
        raise ValueError("title is too long")

    rows: list[tuple[str, str | None]] = []
    source = entries or (MessageEntry(empty_text),)
    for entry in source:
        # Keep pagination at user-text boundaries, before Markdown escaping.
        chunks = _split_plain_text(entry.text, available - 3)
        rows.extend(
            (chunk, entry.target_id if index == 0 else None)
            for index, chunk in enumerate(chunks)
        )

    pages: list[MessagePage] = []
    page_rows: list[tuple[str, str | None]] = []
    used = header_units + 1

    def flush() -> None:
        if not page_rows:
            return
        plain_lines = [header_plain, *(f"• {text}" for text, _ in page_rows)]
        markdown_lines = [
            header_markdown,
            *(f"• {escape_markdown_v2(text)}" for text, _ in page_rows),
        ]
        pages.append(
            MessagePage(
                markdown="\n".join(markdown_lines),
                plain_text="\n".join(plain_lines),
                target_ids=tuple(target for _, target in page_rows if target is not None),
            )
        )

    for text, target_id in rows:
        row_length = 3 + telegram_utf16_length(text)
        if page_rows and used + row_length > limit:
            flush()
            page_rows = []
            used = header_units + 1
        page_rows.append((text, target_id))
        used += row_length
    flush()
    return pages

