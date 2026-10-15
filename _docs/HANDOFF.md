# Pin handoff

Updated: 2026-10-15 (Asia/Qyzylorda). This is a current entry point, not a backlog. Revalidate Git, issues and process handles before resuming.

## Repository and entry points

- Actual checkout: `D:\Program\python\pin`; branch `main`; origin `https://github.com/dystrophyless/pin.git`.
- Root verified local and remote `main` at `454dbaefdbefc3a63092c8f705c25fe188028bdd` before this documentation update. Inspect Git for later commits.
- Read [AGENTS.md](../AGENTS.md), [SDD workflow](sdd/README.md), and [orchestration](orchestration.md).
- Requirements: [brainstorm](PROJECT_BRAINSTORM.md). Approved behavior: [SPEC-001 v0.6](specifications/SPEC-001_pin_mvp.md). Technical design: [architecture](ARCHITECTURE.md). The [task index](specifications/SPEC-001_pin_mvp_tasks.md) links the authoritative GitHub Issues.
- Use project-local Ponytail full; never Superpowers. Executors/Publishers use exact GPT-6 Luna with explicit effort. No implementation Reviewer: the Orchestrator verifies acceptance criteria and raw evidence directly. Do not repeatedly nudge working executors.

## Authority and current scope

The standing objective is to implement and complete remaining issues. The owner authorized local implementation, rule-based task acceptance/publication, commits and pushes. Windows local development only: no Docker, deployment, paid provisioning or further OpenRouter spend. Overall MVP acceptance and public rollout are separate.

Actual Stars payment testing is **removed from required acceptance, not deferred** (owner: "Проверку оплаты не откладываем, а убираем."). Do not retry Test Server top-up or ask to make a real payment. Preserve the payment feature and meaningful local synthetic-event/PostgreSQL verification. Never label that evidence as actual provider operation. SPEC-001 section 11 and issue bodies record the amendment.

The owner selected `https://t.me/pin_appbot` for both the local landing's Telegram CTA and support, using `/support` and `/paysupport`. This does not imply automatic operator ticket routing. Kazakhstan is the intended operating/revenue jurisdiction; entity form (IP/TOO) and statutory financial-retention applicability remain unvalidated. Do not invent a financial or trial-marker retention duration.

## Verified local state

Issues 1–20 are accepted/closed for Windows local development. Latest: [#20 acceptance](https://github.com/dystrophyless/pin/issues/20#issuecomment-6191790583), [contract audit](evidence/issue-20-local-acceptance.md), and [support proof](evidence/issue-20-support-local.md). Acceptance of a local feature is not release acceptance.

The combined frozen product-source baseline is `5464eec0f958c7bf40febf3dfe77ba0e5ed50fe5`: 543 tests, zero failures/errors/skips; Ruff, mypy219, architecture, migration roundtrip/drift, landing and actual cached offline RU/EN/silence checks passed. Injected-transport 50-owner dispatch-start p95 was 3.146 seconds. All 33 manifest fixtures were cleaned; protected databases were preserved. Two pytest-asyncio deprecation warnings remain. [Durable qualification](evidence/local-integration-5464eec.md); raw packet `C:/Users/roman/AppData/Local/Temp/pin-combined-retention-qual-5464eec-20261015`.

Subsequent focused evidence, not a new full-suite total:

- [Core lifecycle](evidence/issue-25-core-local-integration.md): native RU/EN composed appointment journey, 2/2 passed; capture/confirmation, two reminders, check-in, one nudge and Done through actual local producers/consumers with injected interpretation/transport.
- [Paid lifecycle](evidence/issue-26-paid-local-integration.md): two-case file passed 2/2 on a fresh native fixture, zero failures/errors/skips, JUnit51.428s; SHA `0897ddfcc4aea09231ee49071ad4d675b78309a59745bec11e0bc7d408a61957`. Trial/paid quota exhaustion preserves reminders; synthetic receipts/renewals replay once; expiry denies delivery. Confirmed cancellation and refund/replay preserve separate paid coverage, consumed quota, plans and reminders. Root checked raw JUnit/hash/cleanup and independently passed Ruff/mypy. Five second-slice fixtures were cleaned and the exact 174 non-template database baseline restored.
- [Landing destinations](evidence/issue-21-bot-target-local.md): both configured URLs, RU/EN command instructions, six tests, JavaScript syntax and validator pass. Browser checks verified narrow layout/keyboard and actual navigation to the public `pin` / `@pin_appbot` Telegram page with Start Bot entry. No bot message, deployed landing or financial action was performed.
- [Operations runbook](runbooks/operations_local.md) and [evidence](evidence/issue-22-operator-runbook-local.md) map current diagnostic fields to investigation actions. No host alert or restore proof is claimed. [Retention runbook](runbooks/account_deletion_local.md) and [payment runbook](runbooks/payments_local.md) describe existing operator paths.

The [AC evidence matrix](evidence/local-mvp-acceptance-matrix.md) distinguishes implemented/local/native evidence from missing integration/provider/host/legal outcomes. Issues 21–28 remain open with their original whole-task gates; actual Stars testing is no longer one of those gates. Do not manufacture full acceptance from local components or add repetitive tests as substitutes for required external evidence.

## Active assignment

`/root/paid_local_integration` (GPT-6 Luna High) owns only `tests/test_paid_lifecycle_integration_postgres.py` and its evidence document for one further native #26 period-boundary/deletion race scenario. It must report product defects or monetary/permission ambiguities before changing product code. The assignment includes fresh manifest fixtures, exact cleanup, targeted and whole-file focused verification. Root owns Git, handoff and acceptance; Publisher transfers root decisions. Check the agent handle rather than assuming activity from this file. Do not overlap writers.

Untracked `output/` is unrelated mascot work. Preserve it and all other unrelated edits; stage explicit in-scope paths only. There is no pending Stars-payment user action or missing landing support choice.

## Native environment and evidence safety

- PostgreSQL18.6 / version180006: `127.0.0.1:51017`, role `pin_test`, maintenance database `postgres` (not a `postgres` role).
- Exact owned data directory: `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`; binaries: `D:/Program/postgresql/bin`.
- Before creating a test database verify endpoint, role, version, directory, manifest ownership and name absence. Use a fresh assigned prefix. Before dropping, recheck identity and zero active connections; drop only manifest-created names and verify absence plus unchanged nonmanifest catalog. Never reset a fixture to hide interference.
- Never reset/drop `pin_model_adapter_20261013`, `pin_issue20_sandbox_20261014_25eb0b51`, `pin_issue20_sandbox_20261014_33a6ed3b`, or any unowned historical database. Do not reuse `pin_issue18_prepared_20261014_98de` for proof: known queue interference.
- The eight-call/$0.01 model-smoke authorization is exhausted. Seven reported costs sum $0.0002930409; the first ID/cost is unknown, so this is not an exact total bill. No extra calls, unknown-call resubmission or budget reset. Cached offline Whisper and injected tests may continue.
- Dedicated/main/Test Server credentials stay separate in ignored `.env.local`. Never print tokens, chat identifiers, database URLs with secrets or environment dumps. No bot runner is assumed active.
- A startup observer timeout does not mean PostgreSQL stopped; inspect its actual process/readiness and recovery log before any restart. Existing failed qualification/test packets remain preserved. Earlier handoff chronology is available in Git history; it is not current workflow state.

## Updating this checkpoint

Keep only current entry points, authority, verified evidence, live assignments and safety boundaries. Task scope/status/dependencies/evidence belong in issues; accepted changes belong in CHANGELOG. Record stale/missing checks honestly and recheck external state rather than copying historical claims forward.
