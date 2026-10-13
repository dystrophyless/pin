# Pin spec-driven development

Pin uses spec-driven development: versioned requirements and specifications define behavior, while GitHub Issues track execution. Project documentation is written in English.

## Workflow

`Requirements (BR if needed) -> specification -> specification review -> individual task drafts -> task grooming -> package review -> GitHub issues -> implementation -> independent review -> verification -> acceptance -> integration gate -> handoff`

For end-to-end implementation, follow the [development orchestration](../orchestration.md): the main agent coordinates clean-context Planner/Reviewer assignments and GPT-6 Luna Executors/Publishers. A feature request can run the complete authorized cycle without manual stage-by-stage dispatch.

Start with the [workflow and documentation conventions](guides/doc-conventions.md) and [GitHub Issues workflow](guides/github-issues-workflow.md).

| Artifact | Where it belongs | Purpose |
|---|---|---|
| Product brief | [_docs/PROJECT_BRAINSTORM.md](../PROJECT_BRAINSTORM.md) | Preserve decisions, proposals, and unresolved product questions |
| Business requirements | `_docs/business-requirements/BR-NNN_<slug>.md` | User problem, desired outcome, success signals |
| Specification | `_docs/specifications/SPEC-NNN_<slug>.md` | Intended behavior, contracts, invariants, acceptance criteria |
| Unpublished task | `_docs/task-drafts/<spec-id>/<task-slug>.md` | One complete task description, its dependencies, AC, verification, and grooming |
| Execution task, bug, investigation, gate | GitHub Issue | Scope, ownership, dependencies, status, evidence, acceptance |
| Implementation review | PR and issue comments | Changed behavior, review findings, verification, residual risks |
| Session continuity | [_docs/HANDOFF.md](../HANDOFF.md) | Connection, entry points, latest session checkpoint |
| Accepted history | [CHANGELOG.md](../../CHANGELOG.md) | Accepted changes and release history |

## Writing guides and templates

- [Business requirements guide](guides/business-requirements-guide.md) and [BR template](templates/business-requirements.md).
- [Specification guide](guides/specifications-guide.md) and [spec template](templates/specification.md).
- [Task decomposition guide](guides/task-decomposition-guide.md) and [task issue body](templates/task-issue.md).
- [GitHub Issues workflow](guides/github-issues-workflow.md) and [evidence/acceptance comment templates](templates/issue-evidence.md).
- [Documentation conventions](guides/doc-conventions.md).
- [Versioning and changelog guide](guides/versioning-guidelines.md).

## Review skills

- [spec-task-review](../../.agents/skills/spec-task-review/SKILL.md): check a specification and issue against real code and evidence.
- [task-package-review-loop](../../.agents/skills/task-package-review-loop/SKILL.md): resolve inconsistencies across connected specifications and issues before execution.

Invoke them as `$spec-task-review` and `$task-package-review-loop` from a Pin project session. Their definitions live in `.agents/skills/`, with `SKILL.md` frontmatter and `agents/openai.yaml` metadata. Read the relevant skill when its description matches the requested review.

## Examples

- [BR to spec: task capture](examples/br-to-spec-task-capture.md).
- [A complete increment: reminder cancellation](examples/task-reminder-cancellation.md).
- [Review findings and verification evidence](examples/review-and-evidence.md).
- [Pin-specific task draft](examples/pin-task-draft.md), using the existing brainstorm without approving new product decisions.

Examples are hypothetical Pin scenarios. They do not allocate issue IDs, approve product decisions, or claim executed test results.

## GitHub setup

The following local files are ready for a connected repository:

- `.github/ISSUE_TEMPLATE/01_task.yml`
- `.github/ISSUE_TEMPLATE/02_bug.yml`
- `.github/ISSUE_TEMPLATE/03_investigation.yml`
- `.github/ISSUE_TEMPLATE/04_verification_gate.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`

Publish them to the intended repository's default branch to enable the issue chooser. The forms do not assume labels, assignees, a milestone, or a GitHub Project already exist. Optional labels and Project setup are documented in the GitHub guide; they are not created by this package.

Before publication, write and groom each task in its own draft file using the Markdown task template; mark it `not synchronized`. Keep only titles, intended outcomes, and links in the specification's task index. Do not put detailed task bodies in the specification or a combined task document.

After publication, update the index and dependencies to real issue references, replace published local draft bodies with issue-link stubs, and maintain tasks in GitHub. Unpublished drafts stay local. Grooming changes individual drafts/issues; update the spec only for shared requirements or contracts. Assign real GitHub issue numbers only after creation.

## Process size

A small isolated fix can use one issue and a focused check. Cross-component product behavior needs a spec and package review. Migrations, authentication, scheduling, recurrence, external actions, and production changes need explicit failure behavior and evidence proportionate to their risk. A final gate proves integration when individual tasks cannot prove the complete contract.

Requirements, execution scope, acceptance, and external actions follow the user's instructions and the linked issue/specification.
