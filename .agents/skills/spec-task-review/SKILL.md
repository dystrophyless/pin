---
name: spec-task-review
description: Use when a Pin specification or GitHub issue needs an audit for missing behavior, contradictions, unverifiable acceptance criteria, implementation mismatch, or unclear task boundaries.
---

# Specification and task review

Review the requested specification and task as executable contracts against actual code and evidence. Return findings; change files or GitHub records only when fixes or publication are authorized.

## Context

Read [AGENTS.md](../../../AGENTS.md), the requested spec/issue revisions and latest discussion, linked BR, relevant architecture, and [HANDOFF](../../../_docs/HANDOFF.md). Use the [decomposition guide](../../../_docs/sdd/guides/task-decomposition-guide.md) when task boundaries are under review.

A separate task draft owns its full description and grooming before publication; a published issue body owns scope, AC, dependencies, ownership, and workflow status afterward. A spec defines shared accepted behavior and contains only a concise task index with titles, outcomes, and draft/issue links. Identify disagreements explicitly instead of silently choosing the convenient version.

For authorized grooming, edit each unpublished task in its own `_docs/task-drafts/<spec-id>/<task-slug>.md` file or edit the published issue when GitHub updates are authorized. Change the spec only for shared requirement/contract findings. If detailed tasks are embedded in a spec or combined document, flag the structural gap; when restructuring is authorized, extract individual drafts preserving all prior grooming details and replace the aggregate bodies with a concise index. Do not fix the gap by adding further detailed task sections to the spec.

When GitHub, implementation, or runtime evidence is missing, separate what the documents guarantee from what remains unproved. A local issue draft is not a published issue.

## Inspect execution seams

Use the available semantic code search to locate entry points, then read relevant producers/consumers, validators, prompts, parsers, persistence, tests, and resume/finalize paths. Use literal search where every occurrence matters.

Inspect the families affected by the claimed increment:

- State/lifecycle: terminal truth, persistence boundary, cancellation, retries, checkpoint identity, and operator-visible state after partial failure.
- AI/protocol: actual routing/context, output validation, tool authorization, duplicate mutations, and fallback behavior.
- Validator/gate: all producers/consumers, empty/malformed input, actual parser semantics, and adjacent contracts.
- Decomposition: independently shippable outcomes, missing prerequisites/follow-ups, overlapping ownership, and integration proof.
- Reminders: time-zone/date ambiguity, recurrence identity, restart recovery, and subscription/allowance boundaries where the approved scope touches them.

An API cancellation response alone does not prove suppression of a cached or in-flight delivery. Trace the consumer boundary or mark runtime prevention unproved. Apply this check only to contracts involving cancellation.

## Classify evidence

| Status | Meaning |
|---|---|
| confirmed | Direct evidence proves the claim |
| refuted | Direct evidence disproves it |
| partial | A narrower claim is supported; the stronger claim is unproved |
| unconfirmable | Missing context, runtime evidence, or a future decision is required |

Generate hypotheses proportionate to the requested audit. Separate product/runtime bugs, task-doc gaps, architectural seams, and decomposition problems. An intentional exclusion is not automatically a bug, but the claimed increment may require a follow-up.

Assign severity by the affected contract:

- CRITICAL: credible severe data/security damage or an unusable core contract.
- HIGH: the claimed increment or essential invariant cannot be delivered correctly.
- MEDIUM: meaningful bounded risk or missing proof.
- LOW: minor clarity, maintainability, or usability improvement.

Do not report a confirmed runtime bug from documents alone. Identify the exact check that would confirm it.

## Output contract

1. Verdict, reviewed revisions, and evidence limitations.
2. Full severity-ordered actionable findings with stable IDs, evidence status, finding class, impacted AC, concise proof/reference, and a scoped action.
3. For a broad audit, confirmed/refuted/partial/unconfirmable buckets referencing the finding IDs.
4. Next verification or decision for unresolved claims, including expected evidence and environment.

Distinguish proposed checks from executed checks and local checks from deployed evidence. Every prioritized finding must remain accounted for in the detailed report.

## Finish

Return the review. Missing product decisions block dependent implementation; they do not authorize guessing. A review verdict does not change issue status, approve implementation, or authorize GitHub comments. For cross-task inconsistencies, use `task-package-review-loop`.
