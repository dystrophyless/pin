# Pin handoff

Updated: 2026-10-13. Time zone for this checkpoint: Asia/Qyzylorda.

This file is a compact entry point, not a backlog or copied task-status table. Live execution state belongs in GitHub Issues. Verify live state at session start; this checkpoint can become stale.

## Repository connection

| Item | Last verified state |
|---|---|
| Local project | `D:\Program\python\pin` |
| Git repository | Initialized; local branch `main` |
| GitHub repository | [dystrophyless/pin](https://github.com/dystrophyless/pin), verified read-only on 2026-10-13 |
| Issues URL | [Pin Issues](https://github.com/dystrophyless/pin/issues); no issues returned by the live issue search at this checkpoint |
| Optional GitHub Project | Not configured; not required for this workflow |
| Remote default branch | `main`; HEAD `3106612277d3315117546285330c9253ac804c39` at this checkpoint |
| Local Git connection | `origin` points to `https://github.com/dystrophyless/pin.git`; local commits have not been pushed |

Recheck remote state at session start. A `git ls-remote` check on 2026-10-13 confirmed the remote HEAD above. The documentation commit moves the root brief and `docs/ARCHITECTURE.md` into `_docs/`; publish those moves together with the updated entry points when pushing is authorized. Do not maintain duplicate source briefs.

## Current project state

- Existing source brief: [PROJECT_BRAINSTORM.md](PROJECT_BRAINSTORM.md). It is a product brief, not an approved implementation plan.
- [SDD documentation](sdd/README.md) defines the workflow, templates, GitHub issue forms, PR template, project review skills, and examples.
- There is no application code, approved implementation spec, or established application test command in this local project at this checkpoint. No live task queue verification was performed in this commit session. The SDD package is local and has not been pushed.
- Product decisions remain open in the brainstorm, including confirmation policy, date/recurrence semantics, billing, retention, and architecture details. Consult the brief instead of duplicating them here.
- Price, provider-capability, hosting, and benchmark statements in the brainstorm are historical research; revalidate the relevant claims before making production commitments.

## Start or resume work

1. Verify the local checkout, working-tree changes, remote, and selected issue against the intended Pin repository.
2. Read [AGENTS.md](../AGENTS.md) and the [SDD workflow](sdd/README.md).
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

- Issue: none; current work concerns local process documentation and project skills.
- Linked spec: none; no product implementation contract is approved.
- Last completed action: prepare the local SDD documentation commit, reconcile moved-document links and Git connection statements, and verify project-local Ponytail and Superpowers disabling.
- Remote changes: none.
- Next setup action: push the local commits and templates only when explicitly authorized; verify the remote entry points afterward.
- Next planning action: select one product increment from the brainstorm, resolve its blocking decisions, and draft its BR/spec and issue package.

For a future session, replace this checkpoint with the active issue URL, exact revision/worktree boundary, last evidence comment or artifact, unresolved blocker, and next concrete action. Keep historical execution details in issue comments and linked evidence.

## When updating this file

- Update repository connection, entry points, environment limitations, and the latest checkpoint when they change.
- Do not mirror every issue, priority, checklist, or discussion.
- Record synchronization time and any unavailable live checks. A local checkpoint does not change GitHub.
- Do not append all session history; keep the latest checkpoint here and use issues/PRs for durable execution history.
