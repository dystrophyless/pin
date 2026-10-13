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
- [The MVP issue index](specifications/SPEC-001_pin_mvp_tasks.md) links to 28 published issues, with 95 real dependency edges and reverse Blocks. Local published drafts are link stubs. Task implementation, runtime qualification and release evidence remain pending.
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

- Active objective: implement all eligible MVP issues in dependency order, attach revision/environment/results/limitations evidence, and close only after AC, independent review and delivery conditions pass. The user delegated per-issue acceptance and closure; no repeated acceptance question is required.
- Current local HEAD: `eeadfd1` (runtime implementation); scope/spec documentation revision: `af8e026`; base: `45c32f0`. No push, deployment or paid services authorized.
- [Runtime #1](https://github.com/dystrophyless/pin/issues/1) is closed as completed for Windows local development after the evidence comment was read back: locked disposable install (65 packages), four role readiness checks, ruff, mypy (14 files), pytest (17/17), architecture check, invalid-port redaction, and offline lock check. Evidence: Windows 11 AMD64, CPython 3.12.12, uv 0.10.4; packet at `C:\Users\roman\AppData\Local\Temp\pin-runtime-proof-bdf2719e568b4c17bc7470b7b588ab8b`; parent chunk `1158c9` (6.11s). Linux, Docker/container, hosted CI, provider/database connections, deployment, host qualification, and Whisper-model loading/accuracy/latency remain unverified/deferred; this is not full MVP acceptance.
- Ownership: executor owns runtime code/config/lock/CI/tests and runtime README; publisher owns owner-decision spec/issue reconciliation; Orchestrator owns this checkpoint and acceptance routing.
- Owner answers received: send a late missed completion check with one normal follow-up maximum; moving workout five in a ten-week series after four completions moves the remaining six without restarting count; remember prior trial use minimally and restore active paid access without deleted plans. Publisher is recording bounded amendments and issue readbacks.
- [Persistence #2](https://github.com/dystrophyless/pin/issues/2) is closed as completed for the local Windows phase with evidence recorded at revision `c78dd4c` (pytest 40/40, no skips; Ruff, mypy 30 files; PostgreSQL 18.6 local fixture scenarios; migration downgrade/re-upgrade and zero drift). Linux, Docker, hosted CI, production DB, provider, deployment and full MVP/release proof remain deferred.
- [Work #3](https://github.com/dystrophyless/pin/issues/3) and [access #6](https://github.com/dystrophyless/pin/issues/6) are `in_progress` with matching labels after #2 acceptance; their local executors are dispatched, and task acceptance remains pending. [Formatting #4](https://github.com/dystrophyless/pin/issues/4) remains open and blocked: 13 local PostgreSQL/unit tests are reported passing at `c78dd4c`, while live Telegram RU/EN rendered proof or an explicit owner deferral is still pending.
- Next action: continue authorized local execution in #3/#6, record revision/environment/results/limits, and resolve #4's live-render proof or owner deferral before changing its blocked state. Do not infer full MVP/release completion.

Live task state belongs to issues; this checkpoint is not a parallel backlog.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.

Owner override recorded 2026-10-13: implementation reviewers are not required; follow direct executor evidence and Orchestrator verification under the local-only scope.

## Latest execution evidence — 2026-10-13

Telegram presentation #4 has local acceptance at commit `4e9d524`: real dedicated private-chat RU/EN smoke returned four successful pages and the owner confirmed both languages/buttons render correctly. Evidence: [render record](evidence/issue-4-telegram-render.md). GitHub closure remains pending because the connector is currently unavailable; do not infer remote acceptance from this checkpoint.

Durable work #3 and access #6 remain unfinished, with partial source and tests in the worktree. Luna executors were resumed after a usage-limit failure; access core currently passes 13 unit tests. Billing ORM/migration integration waits for the platform migration seam to stabilize. No Docker/deployment/reviewer agents or pushes are authorized for this local phase.
