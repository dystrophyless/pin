# Issue #14 — local text capture acceptance evidence

Date: 2026-10-14, Asia/Qyzylorda. Direct Orchestrator acceptance for Windows local development only. Source: `65615844509dbdf367864dc7bd55f3b4b915bfe6`. All source is local and unpushed.

## Exact-source verification

`git archive` exported the committed source to `C:\Users\roman\AppData\Local\Temp\pin-parent-capture-20261014-14b7\source`. Import provenance identifies that export, not the live checkout. Fifteen new owner-manifest databases were created after verifying the exact owned temporary PostgreSQL data directory.

Environment: Windows 11 build26200 AMD64, CPython3.12.12, PostgreSQL18.6 at port51017. No Docker, hosted CI or deployment.

| Check | Observed result |
|---|---|
| Full `pytest -q`, JUnit XML | **294 passed, zero skipped**, 100.31s; XML skip absence independently asserted |
| `ruff check .` | PASS |
| `mypy src` | PASS, 122 source files |
| `python -m pin.architecture` | PASS |
| Alembic head/current/check, base downgrade, head re-upgrade/check | PASS; single head `conversation_capture_0001`; zero drift before and after |
| Real dedicated Telegram preview delivery | RU message19, EN message20; exact plain text, bold heading, Confirm/Cancel callback buttons; zero saved plans |

The packet contains source/revision/environment/manifests, check logs/JUnit, migration result, Telegram response facts and cleanup facts. Worker-focused application/model/SQL checks passed55 cases; runtime checks passed four PostgreSQL cases and three injected cases. The parent full exact-source result is authoritative.

## Acceptance coverage

- Private text enters the durable sequenced application job, resolves trusted owner/profile, and commits a processing draft/input fence before provider work. Model I/O holds no SQL transaction. The completed normalized result, allowance and provider ledger replay under the stable request key; completed retries do not call transport twice and persisted-started/unknown calls are never automatically resubmitted.
- Collecting and ready states persist across engine restart. The24-hour expiry, owner/version fences, correction invalidation, preview proof and one-use credentials are enforced. Old confirmation after correction, foreign owner, expiry, stale preferences or current denied access cannot create a plan. Ordinary deterministic choices/Confirm/Cancel do not consume another AI turn.
- RU/EN missing-time, untimed, single suggested-time and appointment-duration choices remain explicit choices. Picking a suggestion produces an unsaved preview requiring separate confirmation. Explicit event zones, DST/date resolution, default/override/only/no-reminder semantics, quiet conflicts/omissions and past explicit reminder clarification use the accepted Planning/Notifications projections.
- A mixed one-off/recurring batch remains wholly unsaved until confirmation. The PostgreSQL restart/concurrent-double-confirm/replay cases create one exact plan/series set. Preview reminders equal saved Planning rules and reconciled Notification intents. An injected error after series persistence rolls back all plan/series/rule/proof/outbox fragments.
- Correction context retains pending normalized proposals and invalidates the old approved preview. The model prompt requires complete replacement including unchanged items/clarified fields; an ambiguous single-item replacement of a multi-item draft asks for clarification. Unset fields remain omitted across storage/reparse instead of becoming explicit nulls.
- The ordinary app worker routes capture alongside existing onboarding/agenda and reminder reconciliation. Actual SQL runtime paths cover clarification and confirmation callback ownership/staleness, successful persisted plan, duplicate confirmation, cancellation with no saved plan, and settled/unknown provider replay. Multi-page replies preserve every page and put actions only on the final page. Success comes from the committed command result, not a model's saved claim; cancelling a successfully processed preview does not refund its successful AI processing.
- Capture uses a bounded4096 output-token setting for the app-worker composition; the established512 default and historical model-smoke ceiling stay unchanged. Typed ten-item injected output previews all ten. Explicit length-truncated output cannot become normalized success or a saved claim. The ten-item limit is a documented engineering bound, not a newly invented owner requirement.

## Real Telegram boundary

Using the authorized private test bot, the exported source processed two synthetic RU/EN text turns through the actual bounded assistant with an injected transport, durable inbox/application worker/draft/proof/outbox and the accepted real Telegram delivery lane. Telegram returned messages19/20. Each returned message exactly matched the persisted reply's plain-text projection, retained a bold heading and literal special-character title, and returned the expected `capc`/`capx` action keyboard. SQL delivery attempts were confirmed with matching message IDs; both preview checks found zero saved plans.

The provider was injected: these messages prove real Telegram delivery/rendering of the actual capture pipeline, not live Qwen interpretation quality. No OpenRouter request was made. API success does not prove human reading; callback credentials, bot credentials and chat IDs are not published.

## Cleanup and limits

All15 parent databases, the live preview database, the two stopped capture worker fixtures and two earlier stopped helper fixtures were dropped after exact-cluster/no-active-connection checks; absence was verified. Historical model receipts, intentionally retained older prototypes and unrelated databases were preserved.

Live RU/EN/batch model evaluation remains the qualification gate; the earlier eight-call/$0.01 provider window is exhausted and has not been reset. Existing-plan edits/completion/cancellation routing belongs to #15; voice belongs to #18; full integrated acceptance remains later gates. No implementation Reviewer, push, paid provisioning, Docker or deployment was used. This qualifies the local #14 increment, not complete MVP/release acceptance.
