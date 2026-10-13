# Issue #4: local Telegram rendering evidence

Date: 2026-10-13 (Asia/Qyzylorda). Implementation base: `c78dd4c`.

The owner authorized sending a dedicated test-bot smoke test to the configured private chat. Credentials were read from ignored `.env.local`; neither the token nor chat identifier is recorded here.

Command: `uv run --locked --offline python -m pin.adapters.telegram.presentation.smoke --send`.

Environment: local Windows, locked CPython 3.12.12 / uv environment. The runner validated the exact configured chat as private before sending. Telegram returned successful RU pages 1/2 and 2/2 (message IDs 4 and 5), and EN pages 1/2 and 2/2 (IDs 6 and 7), each with one bold entity. The examples exercised literal MarkdownV2 reserved characters, Unicode/emoji, pagination, typing and localized reply buttons.

The first attempt sent a page but failed while summarizing response metadata: Aiogram entity types are strings. The runner was corrected before the successful four-page run; that earlier page was not deleted. No unknown-send retries occurred.

The owner then explicitly confirmed: "Yes, both languages and buttons display correctly". This provides client-render evidence in addition to API acknowledgment. It does not prove full bot planning flows, callback wiring in future handlers, message read receipts, or deployed delivery.

Additional local runner checks: `uv run --locked --offline pytest tests/test_telegram_smoke.py -q` — 5 passed; scoped Ruff passed; mypy on the smoke module passed. Fixtures cover no network/credential access in dry-run, sanitized exceptions, exact private destination, string entity metadata and missing configuration. Existing presentation evidence at `c78dd4c` covers 11 unit and 2 real PostgreSQL tests.

Orchestrator acceptance: issue #4's presentation criteria and local delivery are satisfied, including owner-confirmed Telegram rendering. GitHub evidence transfer and closure remain pending because the GitHub connector is unavailable in this session. This file preserves the result without claiming a remote update.
