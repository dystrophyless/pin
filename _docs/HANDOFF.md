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
| Remote default branch | `main`; HEAD `3106612277d3315117546285330c9253ac804c39` at this checkpoint |
| Local Git connection | `origin` points to `https://github.com/dystrophyless/pin.git`; local commits have not been pushed |

Recheck remote state at session start. A `git ls-remote` check on 2026-10-13 confirmed the remote HEAD above. The documentation commit moves the root brief and `docs/ARCHITECTURE.md` into `_docs/`; publish those moves together with the updated entry points when pushing is authorized. Do not maintain duplicate source briefs.

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

The active user goal authorizes implementation of eligible published issues in dependency order, local commits, meaningful evidence, issue updates and closure after direct Orchestrator acceptance. GPT-6 Luna High implements features; GPT-6 Luna Low publishes verified facts. Spec/task reviews are separate from execution; **no implementation Reviewer agents** are required. No repeated per-issue acceptance approval is needed. No push, paid provisioning, Docker or deployment is authorized.

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

Delivery's full 50-owner local outer-loop profile measured transport-dispatch-start p95 2.000s, separately from queue pump/claim. It uses fault-free injected bulk transport; host/network/rate-limit qualification is deferred. Actual RU/EN Telegram messages and confirmed SQL attempt IDs qualify the real dedicated-bot send path. API confirmation does not establish human reading. Billing authorization/lifecycle locks and Planning bounded overdue locks preserve current facts until durable attempt-start; sends occur after the SQL transaction ends. [Billing lock proof](evidence/billing-delivery-lock-local.md).

Capture now includes actual app-worker composition, durable draft/proof/callback flow, mixed-batch atomic confirmation, correction/restart/replay, truncation rejection and multi-page outbox. Capture alone requests up to4096 output tokens; the model-smoke default/ceiling remains512. Real preview delivery used injected interpretation, with no saved plans. Broader live model quality is not claimed. Local single migration head is `conversation_capture_0001` after `delivery_recovery_summary_0001`; qualified snapshots have zero schema drift.

Mutations #15 and voice #18 prerequisites were read back accepted/closed; both issues are now open/in_progress with `status:in-progress`. GPT-6 Luna High `/root/implement_mutations` owns Planning mutation modules/schema/tests/docs and new Telegram mutation adapters; `/root/implement_voice` owns Speech modules/inference/files/schema/tests/docs and new Telegram voice adapters. Root owns shared facades, metadata, config/dependencies, worker/capture routing, Identity/Billing/Notifications/Platform seams and acceptance. #17 support still depends on mutations. Preserve occurrence-only Done, count/history-preserving this-and-future reanchoring (June1/15/29 → June8/22/July6), explicit-zone/keep-reinterpret previews, and the approved late-check/recovery-summary policy. SPEC-001 §11 already records the owner's decisions; no product decision is being guessed.

## Next shared integration work

- Mutation feature publishes `preview_mutation` / `confirm_mutation` through `planning.mutations_api`, using persisted owner-bound24-hour preview and real server-token verifier. Terminal events `planning.occurrence_completed` / `planning.occurrence_cancelled` use occurrence UUID and its incremented occurrence version. Native Notifications snapshot/event filters now handle these events in the integration tree. Root verified11 notification tests, including actual confirmed recurring Done/Cancel → outbox → native consumer, target-only invalidation, sibling preservation and stale/replayed event isolation. Whole #15 still requires future cancellation, actual model/callback routing and atomic Identity+Planning zone handoff. New explicit confirmation may qualify a fresh current-preference projection; do not silently stamp legacy approval during generation/delivery or permanently deny a fresh reschedule solely because the old approved profile version changed.
- Mutation migration is `planning_mutation_previews_0001` after `conversation_capture_0001`; `speech_voice_0001` follows it as the single integration-tree head. Both new ORM models are registered in shared Alembic metadata. Applied mutable prototypes are not final migration proof; acceptance uses new exact-source databases.
- Billing voice seam is committed at `39ee65a`: `ResizeVoiceUsage(owner_id, request_id, voice_seconds, at)` / `resize_voice_usage` updates only a live reserved voice hold atomically within its original period, preserving AI units and checking available quota/current expiry. The owner-scoped getter does not create reservations. Exact archived-source verification:43 tests, zero skips; head/base/head and zero drift; guarded fixtures cleaned. See [Billing extension evidence](evidence/billing-voice-resize-local.md). Initial voice reservation uses one AI turn plus positive declared seconds; actual decoded rounded seconds adjust before inference/interpretation.
- Conversation prepared-voice and typed capture continuation are implemented in the integration tree, **not accepted as the full voice feature**. The original live Speech claim/inbox/request key is preserved; a started provider call is never resubmitted solely because its result is missing. Final capture reply, speech completion and job completion commit together. Preliminary root checks:3 actual PostgreSQL scenarios plus37 existing model tests pass, zero skips; scoped Ruff/mypy pass. Native ingress already routes private speech updates into the Speech lane with their original owner arrival sequence; do not replace this with manual enqueue in the only integration proof.
- Speech owns deduplicated queued/processing/failure status events and immediate file cleanup; Delivery owns real sends. One CPU INT8 multilingual-small model is required. Actual offline RU/EN SAPI fixtures are available. Locked faster-whisper1.2.1's filename decoder is incompatible with PyAV19's removed `metadata_errors` argument. The worker verified the required incremental PyAV decode/resample → NumPy-array inference path on actual short offline RU/EN clips with the locked versions; no AV pin or lock change is needed. This is preliminary real-model slice evidence, not whole #18 or 180-second/host qualification.

## Environment and evidence boundaries

- Repository is `D:\Program\python\pin`, even when the app starts in OneDrive. Use explicit workdir. All recent implementation/evidence commits are local and unpushed; inspect Git for current HEAD.
- Owned disposable PostgreSQL18.6 cluster: `127.0.0.1:51017`, user `pin_test`, exact data directory `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Before destructive fixture operations verify this directory and exact owned names. Installed PostgreSQL and unrelated DBs remain untouched.
- Parent #12 and #14 proof databases and stopped helper/feature fixtures were cleaned up with absence checks. Older `pin_issue12_worker_20261014_0e3a` / `pin_issue14_worker_20261014_0e3a` prototypes are intentionally retained; their applied provisional history is not current migration proof. Active fresh guarded worker fixtures are `pin_issue15_mutations_worker_20261014_b52a` and `pin_issue18_voice_worker_20261014_7c41`; root integration fixtures are `pin_issue18_prepared_20261014_98de` and `pin_issue11_terminal_20261014_47cb`. Set their opt-in environment variables per test process, because shell environment is not inherited across tool calls. Billing resize helper/proof fixtures were cleaned; preserve the remaining active names until final proof and deliberate cleanup.
- **Never reset/drop historical `pin_model_adapter_20261013`.** It retains real model receipts. The authorized eight-call/$0.01 smoke window is exhausted: seven reported costs sum $0.0002930409, the first ID/cost is unknown, and this is not an exact total bill. No additional calls, unknown-request resubmission or budget-window reset is authorized. Future live qualification needs its own authorization; injected tests may continue.
- Dedicated test bot credentials/chat ID stay in ignored `.env.local`, never printed. Owner confirmed #4 RU/EN rendering/buttons. Later real test messages qualified exact generated content and persisted attempts, not human reading.
- #12 packet: `C:\Users\roman\AppData\Local\Temp\pin-parent-delivery-20261014-12ac`; #14 packet: `C:\Users\roman\AppData\Local\Temp\pin-parent-capture-20261014-14b7`. Each includes isolated source, manifests, commands/logs/JUnit, migration, Telegram and cleanup facts. Evidence comments carry durable summaries; temp packets are local artifacts.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.

Owner override recorded 2026-10-13: implementation reviewers are not required; follow direct executor evidence and Orchestrator verification under the local-only scope.
