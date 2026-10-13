# Issue #9: accepted local agenda increment

Date: 2026-10-14 (Asia/Qyzylorda). Direct Orchestrator acceptance under the owner's standing implementation/closure authority. GitHub publication/readback follows this checkpoint.

Verified source: `bd0461f12e39f4814e1a9d8cca8da372b8014253`, including accepted Planning source `65c40df`. Local and unpushed. Approved requirements: SPEC-001 section 11 (current v0.5), K04 / AC09, and the unchanged live issue #9 contract. Recurrence's concurrent unfinished source was excluded from this committed snapshot; its working-tree edits and metadata registration were preserved.

## Environment and commands

Locked CPython 3.12.12 on Windows, PostgreSQL 18.6 at loopback 51017 on the guarded disposable cluster. Parent exported committed source to `C:/Users/roman/AppData/Local/Temp/pin-parent-agenda-series-b72c/source`; import provenance confirmed that source. Eleven fresh isolated fixture databases were created: existing integration suites plus agenda, with the otherwise unused recurrence-labelled fixture used only for this agenda render proof. No recurrence verification is inferred from its name. Historical provider-accounting records were untouched.

The parent runner used all applicable PostgreSQL opt-ins and ran `python -m pytest -q`, Ruff, mypy and `python -m pin.architecture` against the snapshot. Full results: **188 passed, no skips, 66.70 seconds**; Ruff passed; mypy passed on 91 source files; architecture boundaries passed (chunk `344685`). Raw provenance, suite and static logs are `check-0.log` through `check-4.log` in the proof directory.

Alembic upgrade/head/current/check, downgrade/base, re-upgrade/head/check passed against the same source, head `agenda_pages_0001`, with zero metadata drift (chunks `f228b3`, `b021b6`). The current working-tree recurrence migration is not part of that accepted snapshot.

## Acceptance scenarios

- Sixteen agenda tests (nine pure/parameterized and seven real PostgreSQL) verify exact owner-isolated membership IDs, half-open local days/weeks and Monday start, DST-short/long boundaries, midnight/event-zone differences, ongoing and ended appointments, timed tasks/deadlines, older untimed overdue items, cancelled exclusion and optional completed views. Untimed items keep saved dates and never acquire fabricated timestamps or persisted overdue states.
- Persisted snapshot membership/order and page starts survive session restart. Rows are reloaded for current details/state; updates, cancellations, expired snapshots and other-owner access are checked. Localized bounded MarkdownV2 output and empty states are covered separately from queries.
- Actual application-worker fixtures persist reply, snapshot/navigation tokens and fenced job receipt atomically, with replay protection. Published Billing VIEW_PLAN checks precede command/callback plan reads: expired non-deleted users keep views; deleted accounts expose no titles and create no usage reservations. Non-agenda callbacks retain their own handlers.
- Authenticated action-specific token version checks preserve valid agenda navigation while a settings draft advances, while rejecting changed profile versions and unknown actions. Integer-version callers stay compatible. Real worker RU/EN stale/expired/replayed callback replies provide /today or /week recovery while retaining safe onboarding state/buttons. The broad suite includes the seven accepted onboarding PostgreSQL regressions.

## Dedicated Telegram render evidence

An isolated fixture used synthetic authenticated private ingress and the actual application worker, then sent its exact generated outbox text/keyboards through the configured dedicated private test bot. No trial or payment was created. Telegram acknowledged:

| Language / case | Message ID | Bold entities | Text UTF-16 units | Keyboard |
|---|---:|---:|---:|---|
| Russian empty today | 12 | 1 | 124 | Показать выполненные |
| Russian long week | 13 | 2 | 2544 | Далее / Показать выполненные |
| English empty today | 14 | 1 | 102 | Show completed |
| English long week | 15 | 2 | 2545 | Next / Show completed |

The long fixtures contain literal Markdown characters and emoji; generated opaque keyboards matched Telegram's returned markup. Typing status covered actual processing. Records: `render.py`, `render-results.json`, `render-schema.py` in the proof directory; result chunk `b90742` confirms source provenance. This establishes Telegram API acknowledgement/parse and keyboard rendering, not a new owner client-screen confirmation or a full live bot conversation. Real button navigation is separately exercised through persisted worker tests; core-gate full dialogue/render qualification remains required.

All eleven parent-owned fixtures and the stopped agenda worker fixture were dropped after checks, with exact-name and cluster guards plus absence checks (chunks `8230ae`, `1ea142`). Proof runners/logs remain outside the repository.

## Limits

No natural-language capture, live recurrence generation, production delivery loop, provider/model call, Docker/deployment, hosted qualification or overall MVP acceptance is claimed. Replies are durable outbox requests; issue #12 owns production delivery. No source was pushed. The original candidate's missing read-access check and callback version/recovery gaps were fixed before this accepted revision and verified with persisted regressions.
