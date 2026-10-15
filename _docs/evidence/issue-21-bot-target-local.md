# Issue 21 bot target local evidence

Date: 2026-10-15 (Asia/Qyzylorda)
Base revision: `ceb30aa77e77a8156d37de4469480004bb551769`
Working tree: shared tree includes concurrent HANDOFF/SPEC/test updates and `output/`; this slice owns `landing/site-config.json`, five landing copy/fallback files, and this evidence file. Other worker artifacts were preserved.

The owner supplied the public username `pin_appbot` and stated: “В @pin_appbot — поддержка через /support и /paysupport”. The config now sets both `telegram_url` and `support_url` to `https://t.me/pin_appbot`. The RU/EN footer tells visitors which commands to use; the privacy copy similarly directs support and payment questions to those bot commands. `/paysupport` is presented as the bot command, not as an automatic operator-ticket workflow. CTA fallback copy is now a user-facing invitation to open Pin in Telegram.

## Checks

Run from `D:\Program\python\pin\landing`:

- `python -m unittest -v test_validate_landing.py` — PASS, 6 tests.
- `node --check app.js` — PASS (exit 0).
- `python validate_landing.py` — PASS, configuration and local links are structurally valid. The validator explicitly does not verify provider eligibility, live reachability, legal compliance, or staging navigation.
- `git diff --check` — PASS.

Local preview at `http://127.0.0.1:4173/`:

- RU and EN rendered the configured Telegram CTAs and support footer link with `https://t.me/pin_appbot`; the enabled links have no `aria-disabled` or negative `tabindex`.
- RU keyboard Tab traversal reached the Telegram CTA and the support anchor. EN's support anchor is visible, has `tabindex=0`, and points to the configured target. The earlier local RU keyboard trace also confirmed the support anchor is reachable. The browser's actual narrow CSS viewport was 383px wide after requesting a 360px viewport; both RU and EN had `documentElement.scrollWidth === clientWidth` (383px), with the support anchor visible and usable. The measured EN anchor was 223px wide.
- Activating the EN support footer link navigated to `https://t.me/pin_appbot`, titled “Telegram: Launch @pin_appbot”. Its page content identifies `pin` / `@pin_appbot`, describes an AI assistant, and offers a “Start Bot” link to `tg://resolve?domain=pin_appbot`. This confirms the public Telegram page resolves to the owner-provided username and presents bot entry. No Start Bot activation, Telegram app takeover, message, or payment was performed.

No hosted landing page, domain, deployment, offer/provider eligibility, legal compliance, actual bot conversation, or payment behavior was checked. The local validator passing does not authorize publication or imply Issue 21 acceptance.
