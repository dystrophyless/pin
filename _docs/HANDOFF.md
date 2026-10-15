# Pin handoff

Updated: 2026-10-14. Time zone for this checkpoint: Asia/Qyzylorda.

This file is a compact entry point, not a backlog or copied task-status table. Live execution state belongs in GitHub Issues. Verify live state at session start; this checkpoint can become stale.

## Repository connection

| Item | Last verified state |
|---|---|
| Local project | `D:\Program\python\pin` |
| Git repository | Initialized; local branch `main` |
| GitHub repository | [dystrophyless/pin](https://github.com/dystrophyless/pin), verified read-only on 2026-10-13 |
| Issues URL | [Pin Issues](https://github.com/dystrophyless/pin/issues); 28 task issues confirmed; live issue bodies own scope, dependencies and workflow status |
| Optional GitHub Project | Not configured; not required for this workflow |
| Remote default branch | `main`; pushed through `c166b6a` on 2026-10-14 |
| Local Git connection | `origin` points to `https://github.com/dystrophyless/pin.git`; `main` was pushed through `c166b6a` on 2026-10-14; inspect Git for later checkpoints |

Recheck remote state at session start. A `git ls-remote` check on 2026-10-14 confirmed the remote HEAD above after the authorized push. The root brief and architecture now live in `_docs/` with their updated entry points published. Do not maintain duplicate source briefs.

## Current project state

- Existing source brief: [PROJECT_BRAINSTORM.md](PROJECT_BRAINSTORM.md). It is a product brief, not an approved implementation plan.
- [SDD documentation](sdd/README.md) defines the workflow, templates, GitHub issue forms, PR template, project review skills, and examples.
- Runtime foundation code, role entrypoints, tests and a dependency lock for issue #1 are accepted for the current Windows local-development phase; see the durable evidence comment on #1. [SPEC-001](specifications/SPEC-001_pin_mvp.md) is the approved MVP baseline; section 11 records the user's resolved product decisions and routine technical details.
- [The MVP issue index](specifications/SPEC-001_pin_mvp_tasks.md) links to 28 published issues, with 95 real dependency edges and reverse Blocks. Local published drafts are link stubs. Accepted local foundations and their evidence are recorded below; remaining implementation, runtime qualification and release evidence stay in the live issues.
- Price, provider-capability, hosting, and benchmark statements in the brainstorm are historical research; revalidate the relevant claims before making production commitments.
## Current authorized phase

Owner decision, 2026-10-13: local development on Windows only, without Docker or deployment. Linux/container/hosted-CI proof, deployment, actual-host qualification, and full release acceptance are deferred until separately authorized. The existing 28-issue MVP contract remains future backlog; this phase does not claim MVP completion or acceptance.

## Start or resume work

1. Verify the local checkout, working-tree changes, remote, and selected issue against the intended Pin repository.
2. Read [AGENTS.md](../AGENTS.md) and the [SDD workflow](sdd/README.md).
   For feature execution, read [orchestration.md](orchestration.md) and establish the current stage, authorized actions, artifact revisions, and model/effort assignments before dispatching subagents.
3. Read the assigned issue and its latest comments, linked spec/BR, dependencies, PRs, and evidence. Check the current `Workflow status` section.
4. If several issues form one behavior change, identify the package and use [$task-package-review-loop](../.agents/skills/task-package-review-loop/SKILL.md) before execution.
5. Resume the first unresolved step within the authorized issue. If GitHub is unavailable, use a local draft explicitly marked `not synchronized`; do not report a live status change.

## GitHub queue navigation

After connection, the repository's Issues page is the entry point. Use search expressions in that repository:

| Purpose | Search expression |
|---|---|
| All open work | `is:issue is:open` |
| Ready tasks, if labels have been configured | `is:issue is:open label:"status:ready"` |
| Work in progress, if labels have been configured | `is:issue is:open label:"status:in-progress"` |
| Blocked work, if labels have been configured | `is:issue is:open label:"status:blocked"` |
| Waiting for acceptance, if labels have been configured | `is:issue is:open label:"status:verified"` |

Labels are optional and must be created before these filtered searches are useful. Without them, inspect the issue body's status. See [GitHub Issues workflow](sdd/guides/github-issues-workflow.md).

## Current execution checkpoint Р Р†Р вЂљРІР‚Сњ 2026-10-14

The active user goal authorizes implementation of eligible published issues in dependency order, local commits, meaningful evidence, issue updates and closure after direct Orchestrator acceptance. GPT-6 Luna High implements features; GPT-6 Luna Low publishes verified facts. Spec/task reviews are separate from execution; **no implementation Reviewer agents** are required. No repeated per-issue acceptance approval is needed. The user explicitly authorized GitHub push on 2026-10-14. Paid provisioning, Docker and deployment remain outside the current authorization.

| Local increment | Accepted source / authoritative evidence |
|---|---|
| Runtime #1 and persistence #2 | `eeadfd1` / `c78dd4c`; closed local foundations; runtime readiness, real PostgreSQL and migration proof in issue evidence |
| Work #3, formatting #4, ingress #5, access #6 | `34f273f`, `4e9d524`, `9be5146` + `a03a487`; [foundation evidence](evidence/issues-3-6-foundations.md), [Telegram rendering](evidence/issue-4-telegram-render.md), [ingress/model](evidence/issues-5-13-local.md) |
| Onboarding #7 / one-off Planning #8 | `353e57d` / `65c40df`; [onboarding evidence](evidence/issue-7-onboarding-local.md), [Planning evidence](evidence/issue-8-planning-local.md) |
| Agenda #9 / recurrence #10 | `bd0461f` / `35a5fbf`; [agenda evidence](evidence/issue-9-agenda-local.md), [recurrence evidence](evidence/issue-10-recurrence-local.md) |
| Intents #11 | `69de470`; 222 tests, zero skips; [evidence](evidence/issue-11-notification-intents-local.md) |
| Delivery #12 | `4d0cd8630a292a0d659eabe3f44038695cecebc7`; 263 tests, zero skips, mypy115, lint/architecture, migration lifecycle/drift0, real RU/EN messages17/18; [evidence](evidence/issue-12-delivery-local.md), [closed/completed acceptance](https://github.com/dystrophyless/pin/issues/12#issuecomment-6155921724) |
| Model foundation #13 | `765fa0a`, compatible live proof `52351aa`; [evidence](evidence/issues-5-13-local.md); broader quality remains qualification work |
| Text capture #14 | `65615844509dbdf367864dc7bd55f3b4b915bfe6`; 294 tests, zero skips (100.31s), mypy122, lint/architecture, migration lifecycle/drift0, real RU/EN unconfirmed previews19/20; [evidence](evidence/issue-14-text-capture-local.md); [closed/completed acceptance](https://github.com/dystrophyless/pin/issues/14#issuecomment-6156086709) |
| Mutations #15 | accepted for Windows local development at `0213a5d289282c746bbb155aaeff3ee68bc39f3d` (tree `10a17bf3c26454d14655159efdfbb09140a8eb4c`); 399 tests, zero failures/errors/skips; [local evidence](evidence/issue-15-mutations-local.md); [closed/completed acceptance](https://github.com/dystrophyless/pin/issues/15#issuecomment-6157978514) |
| Voice #18 | accepted for Windows local development at `83d9b86e049080bcf51f8d54552f23cfc01f7a65` (tree `1db33b0b385ce541985e990633595a2d202c161e`); [local evidence](evidence/issue-18-voice-local.md); [closed/completed acceptance](https://github.com/dystrophyless/pin/issues/18#issuecomment-6157772036) |

Delivery's full 50-owner local outer-loop profile measured transport-dispatch-start p95 2.000s, separately from queue pump/claim. It uses fault-free injected bulk transport; host/network/rate-limit qualification is deferred. Actual RU/EN Telegram messages and confirmed SQL attempt IDs qualify the real dedicated-bot send path. API confirmation does not establish human reading. Billing authorization/lifecycle locks and Planning bounded overdue locks preserve current facts until durable attempt-start; sends occur after the SQL transaction ends. [Billing lock proof](evidence/billing-delivery-lock-local.md).

Capture now includes actual app-worker composition, durable draft/proof/callback flow, mixed-batch atomic confirmation, correction/restart/replay, truncation rejection and multi-page outbox. Capture alone requests up to4096 output tokens; the model-smoke default/ceiling remains512. Real preview delivery used injected interpretation, with no saved plans. Broader live model quality is not claimed. Local single migration head is `conversation_capture_0001` after `delivery_recovery_summary_0001`; qualified snapshots have zero schema drift.

Mutation #15 is accepted and closed for Windows local development at `0213a5d289282c746bbb155aaeff3ee68bc39f3d`. Its accepted Billing gate checks current access for selected home-zone reinterpretation and reminder changes at preview and locked confirmation; Keep-only profile behavior is preserved. The archived qualifier itself passed all 399 tests with zero failures/errors/skips, plus Ruff, mypy152, architecture, migration lifecycle and actual RU/EN/silence recognition. See [Issue #15 evidence](evidence/issue-15-mutations-local.md). Issues #16 and #17 are now in progress with assigned executors because their remaining prerequisites are accepted and TR-01 is resolved; their task contracts remain unchanged. Preserve occurrence-only Done, count/history-preserving rescheduling, explicit-zone/keep-reinterpret previews, and the approved late-check/recovery-summary policy. SPEC-001 Р вЂ™Р’В§11 records the owner's decisions.

## Current checkpoint Р Р†Р вЂљРІР‚Сњ 2026-10-14

Issue #15 mutation integration is committed at `e1ada7311a723161218ae8369a33c9e2afa3b71a`. Follow-up local commits add mutation runtime tests (`7da69a2`), native delivery interleavings (`4b70324`), selected-zone settings callback tests (`666ba4b`), failed model-replay hold release (`8b2a126`), and actual expired-trial policy tests (`39decb051538199d2df0c0e46610f7510bb05694`). The accepted implementation/evidence commits were pushed through 34f4573; #15 is accepted and closed, #16/#17 are in progress with assigned executors, and #18 remains accepted/closed. See [Issue 15 local evidence](evidence/issue-15-mutations-local.md).

The accepted Issue #15 archive includes 50 focused PostgreSQL cases with zero skips: mutation core5, runtime12, actual-application mutation10, native delivery4, home-zone coordinator13, and native settings6. Expired-trial behavior is specifically covered: Done and Cancel are allowed, while Reschedule is rejected for both an after-expiry preview and a live preview confirmed after expiry, with no AI charge. Selected home-zone settings affect only chosen occurrences/series; stale or foreign correction is a no-op and history/count are preserved. Delivery suppresses old claimed cancel/done/reschedule notices, sends the later version-2 reminder, and truthfully confirms a send already started before concurrent cancellation without future follow-up. Failed model replay after an unknown source fault releases the combined hold, preserves cost facts, and does not resubmit.

The final archived voice qualification is recorded in [Issue #18 evidence](evidence/issue-18-voice-local.md); the voice worker and prepared-capture checks are included in its accepted full-suite result. No further live OpenRouter requests, Docker or deployment are authorized. GitHub push was explicitly authorized on 2026-10-14.

The earlier archived baseline packets at `C:/Users/roman/AppData/Local/Temp/pin-parent-mutation-voice-20261014-1518b` and `1518c` remain preserved as historical evidence; `1518d` is the exact-source passing qualification for Issue #15. The historical `pin_model_adapter_20261013` remains preserved.

The integration migration chain remains `telegram_mutation_runtime_0001`; mutable worker schema results are not final migration proof. Issue #15 is accepted/closed for local development in [its acceptance comment](https://github.com/dystrophyless/pin/issues/15#issuecomment-6157978514); Issues #16 and #17 are in progress after confirming their dependencies and settled decisions. Issue #18 is synchronized as accepted/closed in [its acceptance comment](https://github.com/dystrophyless/pin/issues/18#issuecomment-6157772036).

## Environment and evidence boundaries

- Repository is `D:\Program\python\pin`, even when the app starts in OneDrive. Use explicit workdir. Implementation/evidence through `34f4573` is published; inspect Git for later local commits and current HEAD.
- Owned disposable PostgreSQL18.6 cluster: `127.0.0.1:51017`, user `pin_test`, exact data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Before destructive fixture operations verify this directory and exact owned names. Installed PostgreSQL and unrelated DBs remain untouched.
- Parent #12 and #14 proof databases and stopped helper/feature fixtures were cleaned up with absence checks. Older #12/#14 prototypes remain intentionally retained and are not current migration proof. Historical `pin_model_adapter_20261013` must never be reset or dropped. The new refund proof fixture `pin_issue18_prepared_20261014_refund_f742` is retained. Do not reuse the older `pin_issue18_prepared_20261014_98de` fixture for proof because of known queue interference. Preserve active fixtures until final proof and deliberate cleanup; set opt-in variables per test process.
- **Never reset/drop historical `pin_model_adapter_20261013`.** It retains real model receipts. The authorized eight-call/$0.01 smoke window is exhausted: seven reported costs sum $0.0002930409, the first ID/cost is unknown, and this is not an exact total bill. No additional calls, unknown-request resubmission or budget-window reset is authorized. Future live qualification needs its own authorization; injected tests may continue.
- Dedicated test bot credentials/chat ID stay in ignored `.env.local`, never printed. Owner confirmed #4 RU/EN rendering/buttons. Later real test messages qualified exact generated content and persisted attempts, not human reading.
- Additional feature-worker fixtures include `pin_issue15_runtime_worker_20261014_5e82`, `pin_issue15_zone_worker_20261014_f83c`, `pin_issue15_zone_runtime_20261014_2ad8`, and Speech worker fixtures `pin_issue18_voice_worker_20261014_7c41` / `pin_issue18_voice_worker_20261014_7c42`. The older `pin_issue18_prepared_20261014_98de` has known queue interference and must not be reused for proof. Do not reset fixtures to hide interference; use fresh owned databases and preserve `pin_model_adapter_20261013`.
- #12 packet: `C:\Users\roman\AppData\Local\Temp\pin-parent-delivery-20261014-12ac`; #14 packet: `C:\Users\roman\AppData\Local\Temp\pin-parent-capture-20261014-14b7`. Each includes isolated source, manifests, commands/logs/JUnit, migration, Telegram and cleanup facts. Evidence comments carry durable summaries; temp packets are local artifacts.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.

Owner override recorded 2026-10-13: implementation reviewers are not required; follow direct executor evidence and Orchestrator verification under the local-only scope.

### Active integration checkpoint

- Local retention implementation committed at 2d9c65b plus whitespace02cc379. [Focused evidence](evidence/issue-23-retention-local.md): fresh native5/5, ledger/architecture10/10, mypy218 and whole-source/tests/landing Ruff passed; two feature fixtures were cleaned with exact-cluster receipts. Root assigned a new frozen full qualifier at explicit 02cc379; not yet a combined PASS. Output mascot artifacts are unrelated and left untracked.
- A separate narrow local issue22 follow-up /root/operations_retention_projection now owns the read-only retention projection and operator composition, replacing only the obsolete deletion-unavailable placeholder with actual persisted counts/states. Hosted restore status remains unavailable; whole22/23 acceptance is not implied. Qualifier source stays frozen before this follow-up.

- Startup/landing correction committed as 4e5f527. Root inspected the 37-case JUnit (zero failures/errors/skips, 56.857 seconds), safe-field/static-event diff and independently passed Ruff for landing/startup surfaces. Existing redaction negatives remain; raw exception messages are not logged. This focused correction does not retroactively change the frozen 1a1fdc7 FAIL. Retention remains uncommitted and needs its own native/race/restore proof before a new combined gate.
- The 1a1fdc7 packet is now complete: actual cached offline RU/EN/silence passed, local 50-owner p95 2.597 seconds, migration/landing/32-fixture cleanup passed; overall remains FAIL due two startup assertions and five Ruff E501 findings. README and executor-receipt.json are retained in the packet. Protected historical/sandbox databases remain present.

- Resume: usage tool confirmed ordinaryUsageAllowed=true. The same three executors were resumed with their original ownership. Qualification resumes only missing offline speech/final receipt; its 522/524 failed pytest/static logs and completed cleanup stay preserved. Startup executor additionally owns formatting-only corrections for the five landing Python E501 findings. Retention may perform bounded fresh-fixture native checks, coordinating around Whisper memory use.

- 2026-10-15 interrupted-executor checkpoint: /root/retention_local_issue23, /root/fix_startup_diagnostics and /root/qualify_combined_local terminated with usage-limit errors (reported retry 06:04). Do not treat partial retention source as accepted. No matching qualification Python/uv process remained when root inspected after the limit. Preserve dirty source and resume the same bounded tasks rather than replacing implementations.
- Frozen 1a1fdc7 packet `C:/Users/roman/AppData/Local/Temp/pin-combined-qual-1a1fdc7-20261015` completed pytest: 522/524 pass, 2 failures, zero errors/skips, 337 seconds. Failures: startup configuration field names and missing speech dependency guidance were fully redacted. Ruff additionally failed five E501 lines in landing Python files. Mypy and architecture passed; migration lifecycle/checks and landing functional checks passed. Actual offline speech and final packet receipt remain unfinished. Overall result is FAIL, not acceptance. Root initiated exact manifest-scoped cleanup after verifying no live qualifier process; inspect cleanup receipts before any retry.

- Local integration source is 1a1fdc7: Accountability fixture/history correction 8bf2622, bilingual static landing b5d752c, and redacted operations/heartbeat/readiness foundation 1a1fdc7. Parent Ruff, mypy192, architecture and 11 logging/readiness tests passed; native operations drift checks passed after both upgrade and downgrade/upgrade. Whole issues 20–23 are not accepted. A fresh frozen combined qualification is assigned to /root/qualify_combined_local; results are pending.
- Local landing evidence is recorded in [issue 21 preparation](evidence/issue-21-landing-local.md). Actual public bot/support targets, provider offer proof and hosted navigation remain missing. The blank-target publication validator correctly blocks publication.
- Root authorized unaffected local issue 23 implementation under /root/retention_local_issue23. This is not a whole-task readiness waiver: financial jurisdiction evidence, actual payment proof and hosted backup/replay durability remain gates. Preserve the approved no-new-trial/active-paid-access return rule without restoring deleted plans or inventing a retention duration.

- Issues 1–19 are accepted/closed for Windows local development. Latest acceptance: [issue 19](https://github.com/dystrophyless/pin/issues/19#issuecomment-6167390662), [durable evidence](evidence/issue-19-expiry-local.md). Accepted source f8ade5d: 485 tests passed, zero failures/errors/skips; static, migration and actual offline RU/EN/silence checks passed. Thirty fixture databases were cleaned with absence proof.
- Issue 20 remains open/in_progress under GPT-6 Luna High. Current source fe5ce2e preserves explicit `/cancel <UUID>` task preview/confirmation and bare `/cancel` Billing renewal cancellation; active Identity drafts retain precedence. Native cancellation races and Billing route passed. Paid-user access to a general bare task-cancel menu remains an explicit UX question, not an implemented disambiguation feature. No issue 20 acceptance or closure.
- Failed qualification packets are preserved: 55fcbfc diagnosed fake-poller CPU spin; e95c0a6 had 507/509 passing cases; 24199e2 had 508/510 passing cases with two paid task-cancellation failures. Their exact owned fixtures were cleaned with absence receipts. Static, migration and offline speech checks passed. Frozen 42e4f38 qualification completed: 508/510 pass, zero errors/skips, 328.67 seconds; failures are bounded precheckout lock denial and dispatch p95 11.935 seconds against 10. Static/migration/offline speech passed; all 31 fixtures were cleaned/absent, both sandbox DBs preserved. Packet: C:/Users/roman/AppData/Local/Temp/pin-parent-payments-42e4f38-20261014. Corrective source fe5ce2e bounds DB lock waits and dispatches ready work before due sweeps, preserving maintenance and Billing priority. Focused native checks passed: payment 7/7, delivery 30/30, 50-owner p95 2.064 seconds. Frozen fe5ce2e qualification completed with 511/512 passing, zero errors/skips; sole failure was a stale Accountability test expectation. Static, migration, offline speech and 31-fixture cleanup passed. Packet: C:/Users/roman/AppData/Local/Temp/pin-parent-payments-fe5ce2e-20261014. Test-only correction 8bf2622 isolates owner/current-generation work and preserves confirmed-send history; native whole-file proof passed 21/21, zero failures/errors/skips, JUnit 12.647 seconds at C:/Users/roman/AppData/Local/Temp/pin-issue20-accountability-final3-20261015. Combined final-source qualification remains pending. [Published progress](https://github.com/dystrophyless/pin/issues/20#issuecomment-6172683552).
- Authorized Computer Use completed dedicated Telegram Test Server onboarding, terms acceptance and invoice creation. The production 2592000-second period was rejected with SUBSCRIPTION_PERIOD_INVALID. Telegram's TDLib maintainer documents accelerated Test DC periods of 60 or 300 seconds. A temporary, process-local 300-second policy produced a real 250-XTR recurring invoice link; it cannot prove production 30-day expiry.
- Test Stars top-up through explicitly labelled Checkout (Test), using a public test card and fictitious details without saving credentials, did not produce a positive balance. No Pin precheckout approval or successful payment receipt was observed. Related Telegram Desktop issue 29956 reports restrictions on free Test Stars; this is historical supporting evidence, not proof of universal current availability. Root asked the owner to choose deferral or explicitly authorize one real 250-Star development-bot payment followed by renewal cancellation/refund. The answer is pending; no real financial action is authorized.
- Original sandbox session 31274 ended with timeout; accelerated session 23842 exited after operator Ctrl+C. Preserve sandbox databases pin_issue20_sandbox_20261014_25eb0b51 and pin_issue20_sandbox_20261014_33a6ed3b. Temp proof directory: C:/Users/roman/AppData/Local/Temp/pin-issue20-sandbox-proof. Product constants remain 30 days. PIN_SANDBOX_BOT_TOKEN and PIN_BOT_TOKEN remain separate in ignored .env.local; never print secrets/identifiers or fall back silently to the main endpoint.
- Issues 21–23 depend on issue 20; no whole-task completion or prerequisite waiver. Root authorized unaffected local static presentation preparation for issue 21 under GPT-6 Luna Medium /root/landing_local_issue21; [assignment](https://github.com/dystrophyless/pin/issues/21#issuecomment-6172922449). Whole issue 21 remains open/blocked pending provider-tested offer and staging evidence. Issues 24–28 retain deferred/blocked hosted, deployment, qualification and release gates. Local proof does not establish overall MVP acceptance. No Docker, deployment, paid provisioning or extra OpenRouter spend; no invented financial retention duration or model quality threshold.
- Remote main last verified c166b6a; local main fe5ce2e and root documentation changes await publication. User authorized commits/push. Root owns HANDOFF/audit/Git/publication; Luna Executors provide raw evidence and Luna Low Publisher transfers recorded decisions. No implementation Reviewer. Preserve historical/failed fixtures, especially pin_model_adapter_20261013; fresh exact-cluster guarded, manifest-scoped operations only.
