# Pin handoff

Updated: 2026-10-16 (Asia/Qyzylorda). This is a current entry point, not a backlog. Revalidate Git, issues and process handles before resuming.

## Repository and entry points

- Actual checkout: `D:\Program\python\pin`; branch `main`; origin `https://github.com/dystrophyless/pin.git`.
- Current pushed documentation checkpoint: `2afccdd`; inspect Git for later commits and active source changes before resuming.
- Read [AGENTS.md](../AGENTS.md), [SDD workflow](sdd/README.md), and [orchestration](orchestration.md).
- Requirements: [brainstorm](PROJECT_BRAINSTORM.md). Approved behavior: [SPEC-001 v0.13](specifications/SPEC-001_pin_mvp.md). Technical design: [architecture](ARCHITECTURE.md). The [task index](specifications/SPEC-001_pin_mvp_tasks.md) links the authoritative GitHub Issues.
- Use project-local Ponytail full; never Superpowers. Executors/Publishers use exact GPT-6 Luna with explicit effort. No implementation Reviewer: the Orchestrator verifies acceptance criteria and raw evidence directly. Do not repeatedly nudge working executors.

## Authority and current scope

The standing objective is to implement and complete remaining issues. The owner authorized local implementation, rule-based task acceptance/publication, commits and pushes. Windows local development only: no Docker, deployment, paid provisioning or further OpenRouter spend. Overall MVP acceptance and public rollout are separate.

Actual Stars payment testing is **removed from required acceptance, not deferred** (owner: "Проверку оплаты не откладываем, а убираем."). Do not retry Test Server top-up or ask to make a real payment. Preserve the payment feature and meaningful local synthetic-event/PostgreSQL verification. Never label that evidence as actual provider operation. SPEC-001 section 11 and issue bodies record the amendment.

The owner selected `https://t.me/pin_appbot` for both the local landing's Telegram CTA and support, using `/support` and `/paysupport`. This does not imply automatic operator ticket routing. Kazakhstan is the intended operating/revenue jurisdiction; entity form (IP/TOO) and statutory financial-retention applicability remain unvalidated. Do not invent a financial or trial-marker retention duration.

## Verified local state

Latest full frozen qualification `2b03278` is **PASS551/551**, zero failures/errors/skips, JUnit368.413s (rawpytest5warnings). Ruff, mypy219, architecture, migration roundtrip/drift, configured landing and cached offline Whisper RU/EN/silence pass. The 50-owner injected-transport dispatch-start p95 is4.270s. Root independently checked456 file hashes, JUnit and live catalog: all37 manifest fixtures absent, exact174baseline restored and protected3present. [Current qualification](evidence/local-integration-2b03278.md) includes the recurring stale-version fix and all three operations cases. Historical [993412d PASS549](evidence/local-integration-993412d.md) and [12aa FAIL547/549](evidence/local-integration-12aa6b5.md) remain unchanged. The later fourth operations context-fault case is outside the current frozen archive and requires separate evidence.

Issues 1–20 are accepted/closed for Windows local development. Latest: [#20 acceptance](https://github.com/dystrophyless/pin/issues/20#issuecomment-6191790583), [contract audit](evidence/issue-20-local-acceptance.md), and [support proof](evidence/issue-20-support-local.md). Acceptance of a local feature is not release acceptance.

Focused evidence (core/support/paid cases are included in the 551-test frozen run; the fourth operations projection case was qualified separately):

- [Core lifecycle](evidence/issue-25-core-local-integration.md): native RU/EN composed appointment journey, 2/2 passed; capture/confirmation, two reminders, check-in, one nudge and Done through actual local producers/consumers with injected interpretation/transport.
- [Paid lifecycle](evidence/issue-26-paid-local-integration.md): final three-case file passed 3/3 on a fresh native fixture, zero failures/errors/skips, JUnit57.068s; SHA `529fbea3f0bd5586439a00a95834f1bd0f857c7f8e7dfc7ad36dde6f49f9b924`. Trial/paid quota exhaustion preserves reminders; receipts/renewals replay once; expiry denies delivery. Cancellation/refund preserve separate coverage, consumed quota and plans. The final case races renewal/old-period settlement/dispatch, confirms deletion across an already-started send, purges operational state and rejects post-delete replay after refund while retaining financial audit. Root checked raw JUnit/hash/final cleanup, independently passed Ruff/mypy, and verified no issue26 fixture databases remain while protected databases are present. Exact 174 non-template baseline restored; failed harness packets remain preserved. No product bug or source correction was needed.
- [Landing destinations](evidence/issue-21-bot-target-local.md): both configured URLs, RU/EN command instructions, six tests, JavaScript syntax and validator pass. Browser checks verified narrow layout/keyboard and actual navigation to the public `pin` / `@pin_appbot` Telegram page with Start Bot entry. No bot message, deployed landing or financial action was performed.
- [Operations fault proof](evidence/issue-22-faults-local.md): three native cases pass, zero failures/errors/skips, JUnit16.967s; adds all-lane queue/lease/heartbeat fault states, bounded IDs, private-content redaction and read-only projection checks. Product source unchanged. Root inspected test/JUnit/hash/catalog receipts and independently passed Ruff/mypy and live cleanup checks. Exact174 baseline restored, protected3present. The initial no-collection shell invocation is preserved; no hosted alert/restore acceptance is claimed.
- [Operations runbook](runbooks/operations_local.md) and [evidence](evidence/issue-22-operator-runbook-local.md) map current diagnostic fields to investigation actions. No host alert or restore proof is claimed. [Retention runbook](runbooks/account_deletion_local.md) and [payment runbook](runbooks/payments_local.md) describe existing operator paths.

The [AC evidence matrix](evidence/local-mvp-acceptance-matrix.md) distinguishes implemented/local/native evidence from missing integration/provider/host/legal outcomes. Issues 21–28 remain open with their original whole-task gates; actual Stars testing is no longer one of those gates. Do not manufacture full acceptance from local components or add repetitive tests as substitutes for required external evidence.

## Active assignment

The verified product baseline is2b03278 (full551 native/offline cases; qualification docs544358a), with the later operations projection module qualified separately4/4 at9515ef2. Both are local evidence, not overall MVP acceptance. Quality decisions in SPEC-001v0.13 now require four independent groups (RU text/voice, EN text/voice),100unambiguous requests each, at least99successful original requests in each; wrong temporal proposals, unnecessary temporal clarification and no-plan/returned errors are unsuccessful. Actual corpus/recordings, workload and model/host measurements remain unapproved. One corpus question is pending: add recurring-task candidates before approval (recommended), or approve the proposed one-off set with recurrence checked separately. Do not infer an answer from elapsed time.

The owner also confirmed elapsed-from-now requests anchored to the bot's receipt of the message despite delayed processing. Offline reproduction showed a real context-information gap: production factories receive an aware instant but DTO/prompt retain only date. The newly confirmed path is not covered by the historical551qualification.

- Executor `/root/final_frozen_local_qualification` owns the bounded production receipt-time propagation fix, its affected tests and `issue-25-reference-clock-local.md`. Scope includes polling/webhook wall-clock arrival through ingress/platform inbox: the former DB-generated receipt could occur after arrival. Preserve original durable receipt across queued text/voice/transcript/retries and deduplication; keep current execution clock for access/leases/past checks and monotonic payment budgets separate. Native tests may use fresh guarded manifest fixtures only; no other native work runs in parallel. No provider calls or budget-cap changes are authorized.
- Executor `/root/paid_local_integration` completed the proposed400-slot candidate manifest and coverage note, pushed at `2afccdd`:100distinct scripts per language paired across text/voice, with10elapsed-from-receipt cases per language. Root checked group counts, pair equality, null audio and receipt arithmetic. These are unapproved scripts and empty voice slots, not collected recordings or evaluated quality. No active corpus writer remains.
- Publisher completed SPECv0.13 decision/finding updates on #25/#27/#28, linked the candidate on #27 and corrected the stale pending fourth-operations-test wording on #25/#28. AC/dependencies/openblocked status were preserved. Root owns direct AC verification, Git, source/doc integration and eventual frozen qualification. Do not declare the new source path fixed or a new full-suite PASS until raw proof is inspected.

All external deployment/hosted recovery, further provider spend, legal financial-retention applicability and release acceptance limits remain in force. Revalidate live handles and dirty boundaries before resuming; preserve others' work.

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
