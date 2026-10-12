# GitHub Issues workflow

Pin repository: [dystrophyless/pin](https://github.com/dystrophyless/pin). Queue: [Issues](https://github.com/dystrophyless/pin/issues).

## What lives where

BR/spec files are versioned contracts in `_docs/`. GitHub Issues are the execution records. PRs carry implementation diffs and review. HANDOFF links to these records and preserves only the latest session checkpoint.

Do not create a second local task backlog. A local Markdown issue draft is temporary and must be marked `not synchronized`; it is not a GitHub issue until created.

## Issue identity and contents

Use the GitHub-assigned issue number. Titles describe the result, such as `Persist a confirmed task without duplicates`.

Each executable issue needs:

- Kind and `Workflow status`.
- Linked spec/BR or justified exemption.
- One increment, scope, out-of-scope, implementation anchors, and invariants.
- `Depends on`, `Blocks`, ownership, and unresolved decisions.
- Acceptance criteria, verification plan, expected outcomes, and delivery conditions.

Update the issue body when the task contract changes. Record execution, review, and acceptance in comments using [evidence templates](../templates/issue-evidence.md). Preserve old evidence and mark it superseded when necessary.

## Lifecycle

The issue body heading `Workflow status` is the canonical task state. In an issue form this heading is generated from the field label. Edit it to reflect the current state; do not rely on the original dropdown submission indefinitely.

| Status | Meaning | GitHub state |
|---|---|---|
| `draft` | Scope or decisions still being refined | Open |
| `ready` | Required decisions, dependencies, scope, and verification are executable | Open |
| `in_progress` | Authorized execution has begun | Open |
| `blocked` | A named prerequisite or decision prevents progress; record resume condition | Open |
| `verified` | Agreed checks passed with evidence; waiting for acceptance/delivery | Open |
| `accepted` | Owner explicitly accepted the concrete result and delivery conditions are met | Closed |
| `cancelled` | Owner explicitly withdrew the work; record reason | Closed |

Normal progression: `draft -> ready -> in_progress -> verified -> accepted`. Any open state can become blocked; record the previous state and condition for resuming. A cancelled task is distinct from accepted work. Reopening an accepted issue requires a recorded reason and an updated workflow state; a new independent problem usually belongs in a new issue.

## Dependencies and priority

Keep dependencies in the issue body using `Depends on: #123` / `Blocks: #124` or full URLs. Verify actual readiness of prerequisites. Issue-number order does not establish execution order.

Priority and assignee live on the issue. Optional labels/Projects fields may mirror them. Do not maintain copies in HANDOFF or a local task-status table.

## Optional labels and GitHub Projects

These conventions are recommendations, not installed configuration:

- Status labels: `status:draft`, `status:ready`, `status:in-progress`, `status:blocked`, `status:verified`, `status:accepted`, `status:cancelled`.
- Type labels: `type:task`, `type:bug`, `type:investigation`, `type:gate`.
- Priority labels: `priority:p0`, `priority:p1`, `priority:p2`, `priority:p3`.

If labels are used, maintain one workflow status label at a time and keep it consistent with the body. A GitHub Project can provide a board, but it is optional; its Status field is a view of the canonical issue status, not an independent authority.

The prepared issue forms have no automatic labels or assignees so they do not depend on configuration that may be missing.

## PRs, acceptance, and closure

- Link work before acceptance with `Related to #123` and an ordinary issue URL. It creates a reference without requesting automatic closure.
- Avoid `Closes`, `Fixes`, or `Resolves` references in PR bodies and commit messages before acceptance. GitHub can close an issue when those references reach the default branch.
- A merged PR is implementation delivery evidence; it is not automatically product acceptance or a verified deployment.
- After verification, the owner can explicitly accept the concrete revision/evidence. Record the acceptance and whether merge/deploy is part of the agreed delivery conditions.
- Close only when acceptance and those conditions are met, or the owner explicitly cancels the issue. If acceptance occurred before merge but merge is required, remain `verified`/open until delivery is complete and record the acceptance comment.

An agent must have authorization to post comments, edit issues, merge, or close. Explicit user instructions govern those actions; this workflow does not itself grant permission.

## Setup and live access

Local forms are under `.github/ISSUE_TEMPLATE/`; publish them on the repository's default branch to make them available in the chooser. No remote forms, labels, Project, or issues are created by preparing this package locally.

If GitHub access is unavailable, report the limitation, prepare the requested local draft, and mark synchronization pending. Do not claim an issue was updated, accepted, or closed.

## GitHub documentation

- [Creating issue forms](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-issue-forms).
- [GitHub form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
- [Configuring issue templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository).
- [Linking a PR to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).
