# Pin handoff

Updated: 2026-10-13. Time zone for this checkpoint: Asia/Qyzylorda.

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

## Last session checkpoint

- Active objective: implement eligible issues in dependency order within the Windows local-development phase, attach revision/environment/results/limitations evidence, and close after AC, direct Orchestrator verification and delivery conditions pass. The user delegated per-issue acceptance and closure and removed implementation Reviewer agents; no repeated acceptance question is required.
- Accepted implementation revisions: runtime `eeadfd1`, persistence/presentation `c78dd4c`, Telegram smoke proof `4e9d524`, smoke bootstrap fix `e03ecbc`, durable work/access foundations `34f273f`; scope/spec documentation `af8e026`; base `45c32f0`. Read Git for the current HEAD. No push, deployment or paid provisioning authorized. Only the bounded provider smoke described below is authorized.
- [Runtime #1](https://github.com/dystrophyless/pin/issues/1) is closed as completed for Windows local development after the evidence comment was read back: locked disposable install (65 packages), four role readiness checks, ruff, mypy (14 files), pytest (17/17), architecture check, invalid-port redaction, and offline lock check. Evidence: Windows 11 AMD64, CPython 3.12.12, uv 0.10.4; packet at `C:\Users\roman\AppData\Local\Temp\pin-runtime-proof-bdf2719e568b4c17bc7470b7b588ab8b`; parent chunk `1158c9` (6.11s). Linux, Docker/container, hosted CI, provider/database connections, deployment, host qualification, and Whisper-model loading/accuracy/latency remain unverified/deferred; this is not full MVP acceptance.
- Ownership: executor owns runtime code/config/lock/CI/tests and runtime README; publisher owns owner-decision spec/issue reconciliation; Orchestrator owns this checkpoint and acceptance routing.
- Owner answers received: send a late missed completion check with one normal follow-up maximum; moving workout five in a ten-week series after four completions moves the remaining six without restarting count; remember prior trial use minimally and restore active paid access without deleted plans. Publisher is recording bounded amendments and issue readbacks.
- [Persistence #2](https://github.com/dystrophyless/pin/issues/2) is closed as completed for the local Windows phase with evidence recorded at revision `c78dd4c` (pytest 40/40, no skips; Ruff, mypy 30 files; PostgreSQL 18.6 local fixture scenarios; migration downgrade/re-upgrade and zero drift). Linux, Docker, hosted CI, production DB, provider, deployment and full MVP/release proof remain deferred.
- [Work #3](https://github.com/dystrophyless/pin/issues/3) and [access #6](https://github.com/dystrophyless/pin/issues/6) are accepted and closed as completed at `34f273f`: parent full suite 77/77 with no skips, mypy 44 source files, Ruff, fresh PostgreSQL migration downgrade/re-upgrade and zero drift, disposable migration database cleanup. Evidence: [#3 comment](https://github.com/dystrophyless/pin/issues/3#issuecomment-6141702995), [#6 comment](https://github.com/dystrophyless/pin/issues/6#issuecomment-6141702597), [local packet](evidence/issues-3-6-foundations.md). [Formatting #4](https://github.com/dystrophyless/pin/issues/4) is accepted and closed, including owner-confirmed dedicated test-bot RU/EN rendering; [GitHub evidence](https://github.com/dystrophyless/pin/issues/4#issuecomment-6141409766).
- [Ingress #5](https://github.com/dystrophyless/pin/issues/5) is accepted and closed at `9be5146` plus crash-test readiness correction `a03a487`: isolated committed-source suite 97/97 with no skips, mypy 48 files, Ruff, architecture and full migration lifecycle/drift checks. All six newly owned proof databases were cleaned up. [GitHub evidence](https://github.com/dystrophyless/pin/issues/5#issuecomment-6143544899); [local packet](evidence/issues-5-13-local.md). Billing public API metadata extension is also verified in that suite and recorded on accepted #6.
- [Model #13](https://github.com/dystrophyless/pin/issues/13) is accepted and closed for the local provider foundation at `765fa0acaee79c0040c3d46b14605f340f43a5ca`. Isolated committed-source regression passed 133/133 without skips, mypy 62 files, Ruff and architecture; focused owner-fencing checks passed 36 tests. Compatible live RU/EN proof at `52351aa` returned the expected Russian untimed task and English timed appointment, verified against durable SQL results and settlement. [Acceptance evidence](https://github.com/dystrophyless/pin/issues/13#issuecomment-6144944787); [local packet](evidence/issues-5-13-local.md). The bounded smoke used all eight calls under its $0.01 exposure ceiling. Seven reported costs total $0.0002930409; the first call has unknown ID/cost, so this is not an exact total bill. No more provider calls are authorized in this smoke window; do not resend the unknown request. Broader accuracy, voice, hosted qualification and full MVP acceptance remain deferred.
- [Onboarding #7](https://github.com/dystrophyless/pin/issues/7) is accepted by direct Orchestrator verification for Windows local development at `353e57d464e9650d130087240faf1cdf64667f3f`; GitHub closure/completed/status:accepted readback confirmed: [acceptance comment](https://github.com/dystrophyless/pin/issues/7#issuecomment-6145769892). Isolated committed-source suite: 147/147, no skips; Ruff, mypy 75 files, architecture, full migration lifecycle/zero drift. Six onboarding PG scenarios and 29 Billing checks cover persisted dialogs, preview/cancel/conflicts, atomic one-trial activation and approved return-policy facts. Sixteen separate OS processes prove RU/EN continuation at every step and confirmation replay without a second profile/trial. Exact generated summaries/keyboards were acknowledged by Telegram as RU message8 / EN message9 with bold/code entities. [Local evidence](evidence/issue-7-onboarding-local.md). All nine newly parent-owned proof databases were cleaned up. Replies remain durable outbox requests; #12 owns production delivery, #15 owns existing-plan zone effects and #23 owns actual deletion coordination. Migration chain: `conversation_model_0002 -> identity_profile_0001 -> billing_trial_activation_0001`. Planning #8 is accepted by direct Orchestrator verification at `65c40df6b4edb69b7893021a3ed7100738136d92`: exact committed snapshot, 172/172 with no skips (54.46s), Ruff, mypy86, architecture and complete migration lifecycle/zero drift. Fourteen Planning PG tests and nine unit cases now include required exact reminder-projection approval, stale-before/during-application rejection, fresh omission notice approval and receipt replay. All nine fresh parent fixtures were cleaned up. [Acceptance evidence](evidence/issue-8-planning-local.md); GitHub closure/completed/status:accepted readback confirmed: [acceptance comment](https://github.com/dystrophyless/pin/issues/8#issuecomment-6152831361). SPEC-001 v0.5 records repeat-from-new-date with remaining count/history preserved (June8/June22/July6); #10/#15/#25 already contain that amendment. #9 agenda and #10 recurrence dependencies were read back accepted/closed; both are dispatched to GPT-6 Luna High executors. Agenda owns read queries/Telegram presentation and application-worker routing; recurrence owns separate series domain/application/infrastructure, migration and metadata registration. Root coordinates shared Planning facade/model seams. Verification and acceptance of these two increments remain pending. No new provider calls, push, Docker, deployment or full-MVP acceptance.

Live task state belongs to issues; this checkpoint is not a parallel backlog.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.

Owner override recorded 2026-10-13: implementation reviewers are not required; follow direct executor evidence and Orchestrator verification under the local-only scope.

## Latest execution evidence — 2026-10-13

Telegram presentation #4 is accepted at commit `4e9d524`: real dedicated private-chat RU/EN smoke returned four successful pages and the owner confirmed both languages/buttons render correctly. Evidence: [render record](evidence/issue-4-telegram-render.md). After connector recovery, Publisher transferred the evidence and read back the closed/completed issue with `status:accepted` on 2026-10-13. No local commits have been pushed.

Durable work #3 and access #6 are committed and accepted. The full regression run also found and fixed the smoke helper's configuration-boundary violation without weakening architecture checks; final parent suite passed 77 tests. New execution ownership: ingress #5 owns Telegram transport/gateway bootstrap and config/migration integration; model #13 owns Conversation provider normalization and provider cost records. Shared config/migration seams must be integrated sequentially. Read live issues for task state; these assignments do not claim implementation or provider verification. No Docker/deployment/reviewer agents or pushes are authorized for this local phase.
