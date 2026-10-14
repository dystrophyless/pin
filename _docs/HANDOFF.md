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
| Remote default branch | `main`; HEAD `34f45733601b7255f9893b2fc8d07b38dc4c16a9` verified on 2026-10-14 |
| Local Git connection | `origin` points to `https://github.com/dystrophyless/pin.git`; `main` was pushed through `34f45733601b7255f9893b2fc8d07b38dc4c16a9` on 2026-10-14; later checkpoints remain local until published |

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

## Current execution checkpoint — 2026-10-14

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

Mutation #15 is accepted and closed for Windows local development at `0213a5d289282c746bbb155aaeff3ee68bc39f3d`. Its accepted Billing gate checks current access for selected home-zone reinterpretation and reminder changes at preview and locked confirmation; Keep-only profile behavior is preserved. The archived qualifier itself passed all 399 tests with zero failures/errors/skips, plus Ruff, mypy152, architecture, migration lifecycle and actual RU/EN/silence recognition. See [Issue #15 evidence](evidence/issue-15-mutations-local.md). Issues #16 and #17 are now in progress with assigned executors because their remaining prerequisites are accepted and TR-01 is resolved; their task contracts remain unchanged. Preserve occurrence-only Done, count/history-preserving rescheduling, explicit-zone/keep-reinterpret previews, and the approved late-check/recovery-summary policy. SPEC-001 §11 records the owner's decisions.

## Current checkpoint — 2026-10-14

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

- `29f8366` adds bounded Identity roster and transaction-bound Planning occurrence/today-untimed read ports. Direct Orchestrator rerun on the assigned PostgreSQL 18.6 cluster: 3 passed, zero skips; whole-source mypy (173 files) and architecture passed. This is foundation progress, not Issue #16 acceptance.
- The existing owned PostgreSQL cluster was stopped; it was restarted at the same data directory without resetting databases. SQL identity and port were verified after crash recovery, before the successful read-port rerun.
- Issue #16 outer delivery/callback composition and native failure/race checks are still in progress. Issue #17 retains the approved text request followed by preview and confirmation; an optional button preference is pending, and bounded advice output validation is being completed. Neither issue is accepted or closed.
- The separate `pin-parent-accountability-support-20261014-1617a` qualification packet has scripts ready for 27 fresh guarded fixtures. It has not frozen a revision or created databases; exact-source inventory and qualification remain pending.
