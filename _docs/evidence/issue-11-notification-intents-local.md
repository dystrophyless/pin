# Issue #11 — local reminder-intent acceptance

Date: 2026-10-14, Asia/Qyzylorda. Direct Orchestrator acceptance covers the Windows local-development increment.

## Source and environment

- Source revision: `69de470b883e0bf79dde8290e3d2c44936e7ba49`, committed locally and not pushed.
- Exact source export: `C:/Users/roman/AppData/Local/Temp/pin-parent-intents-64ed/source`. The import-provenance check resolved `pin` inside this export.
- Windows, CPython 3.12.12, local PostgreSQL 18.6 at port 51017 on the guarded owned temporary cluster.
- Twelve fresh PostgreSQL opt-ins, including `PIN_TEST_NOTIFICATIONS_DATABASE_URL`. The packet at `C:/Users/roman/AppData/Local/Temp/pin-parent-intents-64ed` retains scripts, manifest and check logs; no local secrets were exported.

## Observed checks

The parent ran `verify.py` against the committed-source export:

- Full pytest: **222 passed, zero skips, 62.46 seconds**.
- Ruff: all checks passed.
- Mypy: no issues in 110 source files.
- Architecture import boundaries: PASS.

Result chunk: `dfc9f4`. Focused coverage includes six Notifications policy cases and three PostgreSQL consumer cases, plus fifteen one-off Planning and twelve recurrence PostgreSQL cases and their pure projections.

`migrations.py` passed head upgrade/current/check, base downgrade, re-upgrade and check. Single head: `notification_intents_0001`; both metadata drift checks reported no new upgrade operations (`875abf`). The linear addition is `planning_recurrence_0001 -> planning_reminder_policy_0001 -> notification_intents_0001`.

## Acceptance coverage

- Planning owns calendar/elapsed resolution and past-explicit clarification. Notifications owns a shared pure quiet-hour projection. Default reminders defer only when still useful before the event; otherwise they are omitted with a reason. Explicit quiet-hour times remain unchanged with a visible conflict notice.
- The Friday calendar-day/two-hour fixture retains Thursday 14:00 and Friday 12:00 with offset provenance. Current confirmed canonical reminder lists, not raw language or event payloads, determine the persisted intentions. Raw text/default applicability is integrated by the dependent capture feature.
- Actual one-off and recurring confirmation fixtures compare the approved projection/digest, persisted reminder rules, locked Planning snapshot and final reminder intentions, including quiet deferral and exact due/source/offset/preference versions.
- Preview remains side-effect-free. Intent creation reads committed current Planning rows under caller-owned transaction locks; it does not trust event payload schedules.
- Current plan/occurrence/preference/series versions fence intent writes. Older events converge to the latest qualified snapshot; impossible future versions and wrong owners produce stale receipts without overwriting another owner's intentions.
- Semantic uniqueness and per-event receipts survive restart/replay. Concurrent outer-worker polls produce one intention and one receipt. Terminal state, preference drift and legacy null approval versions invalidate prior intentions and advance dispatch versions.
- Stopping future series generation preserves intentions for already materialized open occurrences. Recurrence generation rejects changed preferences rather than silently stamping a new approval or generating a changed reminder schedule.
- The actual local application worker invokes `process_pending_reminder_schedules` before job claims on every loop, including idle iterations. The bounded consumer selects supported unreceipted events with `SKIP LOCKED`, independently of the transport publication marker. Unsupported events do not starve it; its receipt and intentions share one transaction. Parent inspected the worker hook and current-state/version reader directly; SQL integration tests execute that helper.
- DDD facades and domain boundaries are preserved; no second scheduler or generic lease implementation was introduced.

## Cleanup and limits

All twelve parent-owned fixtures were dropped and verified absent with exact cluster/manifest guards (`8efd9b`). The three stopped worker fixtures were also dropped and verified absent (`6cfee3`). Historical provider evidence was preserved.

This increment performs no external reminder sends. #12 owns dispatch, attempts, recovery and current-access send checks; #14 owns production text capture and preview rendering, and #15 owns user mutation coordination. Confirmation issuers in Planning fixtures remain trusted persisted test producers. No new provider calls, payment calls, push, Docker, deployment, Linux/hosted qualification or full-MVP acceptance is claimed.

GitHub acceptance publication/readback follows this evidence packet.
