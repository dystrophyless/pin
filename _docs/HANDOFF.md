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
- There is no application code or established application test command. [SPEC-001](specifications/SPEC-001_pin_mvp.md) is the approved MVP baseline; section 11 records the user's resolved product decisions and routine technical details.
- [The MVP issue index](specifications/SPEC-001_pin_mvp_tasks.md) links to 28 published issues, with 95 real dependency edges and reverse Blocks. Local published drafts are link stubs. Task implementation, runtime qualification and release evidence remain pending.
- Price, provider-capability, hosting, and benchmark statements in the brainstorm are historical research; revalidate the relevant claims before making production commitments.

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

- Assignment: publish the reviewed MVP task package; do not implement tasks.
- Linked spec: [SPEC-001 v0.2](specifications/SPEC-001_pin_mvp.md), with the real issue index in section 12.
- Last completed action: reconcile existing issues #1–#28 with all reviewed draft hashes, finish real dependency links/readiness statuses, and replace local task bodies with issue-link stubs.
- Live entry point: [Pin Issues](https://github.com/dystrophyless/pin/issues). [Runtime foundation #1](https://github.com/dystrophyless/pin/issues/1) is the next candidate for separately authorized execution; check its current body/status first.
- Remaining owner decisions: late initial check-in/recovery-summary interaction, future-series anchor/count, and deletion-return rights/linkage. Qualification still needs an approved rubric/workload and actual evidence.
- Repository boundary: source documents and issue-index edits are local and unpushed; no commit/push or implementation performed.
- Next action: execute a selected ready issue only when requested; resolve affected owner decisions before dependent whole-task execution.

No local task-status table is maintained. Verify issues and actual prerequisite evidence when resuming.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.
