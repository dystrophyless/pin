# Task decomposition guide

Tasks in Pin are GitHub Issues. Use the [task issue template](../templates/task-issue.md) or the GitHub task form.

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

Review overlapping write surfaces before parallel execution. Two tasks modifying the same contract may be sequential even if their titles appear unrelated. Multi-agent execution is optional.

## Ready checklist

- One outcome and explicit scope/non-goals.
- Linked approved spec, or a justified spec exemption for a small isolated change.
- Relevant implementation anchors or an honest statement that none exist yet.
- Preconditions, dependencies, ownership, and blocking decisions.
- Acceptance criteria covering the intended behavior.
- Reproducible verification plan with expected outcomes and safe test environment.
- Explicit delivery/acceptance conditions where merge or deployment is required.

Do not mark a task ready while its core semantics remain unspecified.

## Execute and close

Use the lifecycle in [GitHub Issues workflow](github-issues-workflow.md). Attach evidence to the issue or PR; leave historical evidence intact when appending a recheck. Mark superseded evidence clearly. Verified work awaits explicit acceptance unless that acceptance has already been provided for the concrete result.

Scope discovered during execution must be classified: required to satisfy the current contract, separate follow-up, or blocked decision. Record the classification and linked issue when one exists; do not invent issue numbers for unpublished drafts.
