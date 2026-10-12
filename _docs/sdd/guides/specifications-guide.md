# Specification guide

Pin uses a specification as a versioned behavior contract; sprint timing and task status live in GitHub. Use the [specification template](../templates/specification.md).

## Purpose

A spec defines what to build, the implementation boundaries, and how to prove the behavior. It links to BR evidence rather than repeating lengthy business justification.

## Required content

- Identity, status, date, source BR/issue/design references, and approval record.
- Goal and one useful product or system increment.
- In-scope and out-of-scope behavior.
- Current implementation anchors, what is reused, what changes, and what must remain unchanged.
- Contracts: inputs, outputs, validation, ownership of persisted truth, state transitions, and relevant API/event semantics.
- Errors and recovery: retries, partial failure, idempotency, cancellation, resume, and observability when relevant.
- Acceptance criteria with observable outcomes, including important negative scenarios.
- Links to the implementation issue package and final integration gate when needed.
- Open questions and explicit implementation blockers.

Add data models, migrations, rollback, rollout, privacy, and performance budgets only where the change requires them. Use concrete constraints; do not invent a schema or precise numerical target without evidence or an accepted decision.

## Separate scope from acceptance

Scope: allow users to reschedule a task.

Acceptance: after the user selects a new time, persisted task time and pending reminders use the new schedule; retries do not create duplicates; obsolete reminders cannot still be delivered according to the approved cancellation contract.

The second statement needs decisions and evidence. A test checking only that a button exists does not prove it.

## Ground the spec

Read relevant code paths, models, validators, prompts, existing tests, and integrations before naming implementation anchors. If no code exists yet, state `no implementation exists`; propose interfaces explicitly and keep approval status visible.

For Pin, specify time-zone/date ambiguity, recurrence, user confirmation, reminder scheduling, and AI-proposed mutation validation where the increment touches them. Do not let an AI output become persisted truth without the agreed validation and authorization contract.

## Decisions and uncertainty

Use a decision table with source, owner, status, and effect on implementation. Avoid vague wording for settled behavior, but preserve uncertainty for genuinely unresolved questions. A blocked decision is better than false precision.

## Traceability

Map `BR requirement -> spec rule -> issue -> verification evidence`. The spec keeps stable requirement IDs and issue links. Issue bodies own live task dependencies/status; spec links are a traceability index, not a second backlog.

## Review and approval

Use [$spec-task-review](../../../.agents/skills/spec-task-review/SKILL.md) to review the specification and task contract, and [$task-package-review-loop](../../../.agents/skills/task-package-review-loop/SKILL.md) for connected issues. Record the exact revision accepted by the owner. A material change to approved behavior needs a decision and updated approval; implementation evidence alone does not approve the change.

A spec can be `draft`, `approved`, `superseded`, or `withdrawn`. Delivery status is evaluated from issue evidence and acceptance, not inferred from the spec's approval status.
