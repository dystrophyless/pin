# Issue 21 bot target local evidence

Date: 2026-10-15 (Asia/Qyzylorda)
Base revision: `b41622c6a5cef2622450529edafe4a94b096a760`
Working tree: already contained unrelated untracked Issue 22 evidence/runbook files, `output/`, and `tests/test_paid_lifecycle_integration_postgres.py` before this slice. This slice changes only `landing/site-config.json` and adds this evidence file; no unrelated artifacts were touched.

The owner supplied the exact public username `pin_appbot`. `landing/site-config.json` now sets `telegram_url` to `https://t.me/pin_appbot` and preserves the blank `support_url` because the real support destination is still pending. The two-key JSON shape is unchanged.

## Checks

Run from `D:\Program\python\pin\landing` on Windows:

- `python -m unittest -v test_validate_landing.py` — PASS, 6 tests.
- `node --check app.js` — PASS (exit 0).
- `python validate_landing.py` — expected `PUBLICATION BLOCKED`, exit 1, solely because `support_url` remains required and blank.

Local browser preview at `http://127.0.0.1:4173/`:

- Russian and English pages each rendered all three `[data-telegram]` links with `href="https://t.me/pin_appbot"`, no `aria-disabled`, and no negative `tabindex`.
- Keyboard Tab traversal reached the Russian header Telegram link. The blank `[data-support]` link had no `href`, `aria-disabled="true"`, and `tabindex="-1"`.
- The Telegram link was not activated. This proves local configured-link and keyboard behavior only; it does not prove that the username resolves, belongs to Pin, reaches a bot, or works in Telegram.

No hosted page, domain, deployment, offer/provider status, support destination, or public navigation was checked. This local configuration does not complete or accept Issue 21.
