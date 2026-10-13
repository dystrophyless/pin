# Task decomposition guide

Tasks in Pin are GitHub Issues. Use the [task issue template](../templates/task-issue.md) or the GitHub task form.

## Drafts, specification index, and grooming

Before publication, create one Markdown file per task at `_docs/task-drafts/<spec-id>/<task-slug>.md`, for example `_docs/task-drafts/SPEC-001/persist_confirmed_task.md`. The slug is a filename, not a separately allocated task ID. Refer to draft paths for dependencies until real issue references exist.

Each draft holds its complete increment, scope/exclusions, ownership/dependencies, implementation anchors, AC, verification plan, delivery conditions, and unresolved decisions. A combined document containing all detailed tasks is not the task package: the package is the set of individual drafts plus the shared specification.

The specification keeps only a concise index with task title, intended outcome, and link to the draft or published issue. Task-specific checklists, implementation steps, and grooming findings stay with the task. Shared requirements/contracts and project-level acceptance criteria remain in the specification.

Use `$spec-task-review` to groom each individual draft. Apply task-local fixes there; update the specification only when a finding changes or exposes a gap in shared requirements/contracts. Use `$task-package-review-loop` to review the individual drafts together without merging their bodies into the specification.

If decomposition or authorized grooming starts from embedded tasks or an aggregate task document, extract each task into its own draft before declaring the package groomed. Preserve all existing decisions, dependency relationships, AC, verification plans, and review improvements. Replace the embedded/aggregate descriptions with an index rather than leaving duplicate authoritative bodies. Do not move unrelated documents or reinterpret accepted requirements.

After authorized publication, copy each reviewed task body to its GitHub issue, resolve draft dependencies to real issue references, and point the spec index to the issues. Replace published draft bodies with short issue-link stubs, recording the published revision when available. Unpublished drafts remain local and marked `not synchronized`; never keep updating a published task in both places.

## One task, one coherent increment

Split by an observable result rather than arbitrary files or layers. A user flow may need UI, API, persistence, and a focused integration check in one task. The boundary is whether these parts form one reviewable outcome.

Separate technical foundation tasks when they have an explicit contract, a concrete verification result, and identified consumers. A migration or backend-only capability can be a valid increment; it must not claim that an unfinished user flow already works.

## Sizing

Prefer a task that can be understood, implemented, reviewed, and verified without unrelated design decisions. Consider the number of independent contracts, lifecycle states, integrations, migration risks, and verification surfaces. File count and a percentage of an unspecified model's context are not acceptance rules.

Split when:

- Two outcomes can ship and be accepted independently.
- Different work requires unrelated decisions or permission boundaries.
- Verification of the result cannot be described clearly.
- The task hides a separate refactor, rollout, or product feature.

Keep one task when splitting would produce misleading claims of completeness or duplicate ownership of a coupled behavior.

## Task kinds

| Kind | Deliverable |
|---|---|
| Product increment | Observable end-to-end behavior |
| Technical foundation | Verified reusable capability or migration |
| Bug fix | Reproduction, corrected behavior, focused regression evidence |
| Investigation | A bounded question, evidence, conclusion, and next decision |
| Verification gate | Package-level proof, limitations, and a release/acceptance verdict |

An investigation does not authorize implementation or production experiments. A gate does not quietly implement a missing feature; findings become linked fixes within authorized scope or follow-up issues.

## Dependencies and ownership

Record `Depends on`, `Blocks`, intended order where necessary, and ownership in issue bodies. Use full issue URLs for cross-repository references. Issue IDs are stable identities, not execution order.
Before publication, keep the same fields in each draft and link dependency drafts by path.

Review overlapping write surfaces before parallel execution. Two tasks modifying the same contract may be sequential even if their titles appear unrelated. Multi-agent execution is optional.

## Ready checklist

- One outcome and explicit scope/non-goals.
- One complete draft file per unpublished task, or one published issue; a spec index entry alone is not a task description.
- Linked approved spec, or a justified spec exemption for a small isolated change.
- Relevant implementation anchors or an honest statement that none exist yet.
- Preconditions, dependencies, ownership, and blocking decisions.
- Acceptance criteria covering the intended behavior.
- Reproducible verification plan with expected outcomes and safe test environment.
- Explicit delivery/acceptance conditions where merge or deployment is required.

Do not mark a task ready while its core semantics remain unspecified.

## Execute and close

Use the lifecycle in [GitHub Issues workflow](github-issues-workflow.md). Attach evidence to the issue or PR; leave historical evidence intact when appending a recheck. Mark superseded evidence clearly. Verified work requires human acceptance or the recorded full-cycle rule-based acceptance in [orchestration.md](../../orchestration.md), plus its agreed delivery conditions. Do not request separate acceptance when the user's delegation already covers the verified result.

Scope discovered during execution must be classified: required to satisfy the current contract, separate follow-up, or blocked decision. Record the classification and linked issue when one exists; do not invent issue numbers for unpublished drafts.
