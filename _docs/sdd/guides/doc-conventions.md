# Workflow and documentation conventions

Use stable document identities and clear ownership of requirements, tasks, and evidence.

## Canonical structure

```text
AGENTS.md
README.md
CHANGELOG.md
_docs/
  PROJECT_BRAINSTORM.md
  HANDOFF.md
  business-requirements/BR-NNN_<slug>.md
  specifications/SPEC-NNN_<slug>.md
  sdd/
    README.md
    guides/
    templates/
    examples/
.github/
  ISSUE_TEMPLATE/
  PULL_REQUEST_TEMPLATE.md
.agents/
  skills/
    spec-task-review/SKILL.md
    task-package-review-loop/SKILL.md
```

## Identity and language

- Write maintained documentation, issue bodies, review evidence, and PR descriptions in English.
- Use `BR-001` and `SPEC-001` as minimum-three-digit IDs; numbers can grow beyond 999. Check existing IDs before allocation.
- Keep IDs and paths stable after approval. Mark a superseded document with a replacement link; do not rename it merely because delivery finished.
- Use GitHub's assigned issue number as task identity. Do not create a parallel local task numbering scheme or infer execution order from IDs.
- Use ASCII snake_case for document slugs. Use `YYYY-MM-DD` dates and explicit time zones when time matters.
- Templates/examples are reusable inputs. Replace instructional placeholders in actual BR/spec/issue artifacts before marking them ready.

## Source ownership

| Information | Authoritative location |
|---|---|
| Product evidence and desired outcome | Approved BR and linked primary sources |
| Accepted behavior, invariants, contracts | Approved spec and decision record |
| Task scope, AC, dependencies, owner, progress | GitHub issue body |
| Execution/review history | Issue comments, PR review, linked artifacts |
| Actual behavior and verification result | Current code and executed evidence |
| Entry points and latest checkpoint | HANDOFF |
| Accepted release history | CHANGELOG |

If two locations disagree, identify the conflict and affected work. A summary must not silently override its source. Optional GitHub labels/Project fields mirror task workflow status; issue body remains the canonical record.

## Statuses

- BR/spec: `draft`, `approved`, `superseded`, `withdrawn`.
- Task workflow: `draft`, `ready`, `in_progress`, `blocked`, `verified`, `accepted`, `cancelled`.
- Issue state: open until accepted with delivery conditions met, or explicitly cancelled. Closure is not automatically proof of deployment.

Do not treat approval of a document as approval of an implementation result. Do not store task execution statuses in a second local backlog. See [GitHub Issues workflow](github-issues-workflow.md).

## References and updates

Use relative links between maintained local documents. In GitHub issues/PRs, link repository documents at a known revision for approvals and immutable evidence. Before a remote exists, record local paths and mark links as not published.

Keep accepted decisions traceable to a user/owner approval and document revision. Record new requirements or material deviations before dependent implementation. Update affected specs/issues together; preserve review history.

HANDOFF changes when entry points, connection, environment, or the latest checkpoint change. CHANGELOG receives accepted behavior changes and their issue/PR references. Neither must duplicate every task action.
