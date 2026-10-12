---
name: task-package-review-loop
description: Use when connected Pin specifications and GitHub issues need review for conflicting contracts, overlapping ownership, missing dependencies, or inadequate integration gates before execution.
---

# Task-package review loop

Review related specifications and issues as one execution contract. An individually well-written task does not establish that the package is coherent or ready.

Read [AGENTS.md](../../../AGENTS.md) and the [GitHub Issues workflow](../../../_docs/sdd/guides/github-issues-workflow.md). Use [spec-task-review](../spec-task-review/SKILL.md) for evidence classes and severity. Return findings first; patch documents or publish GitHub updates only when authorized.

## Freeze the package

Record actual spec revisions and issue references, intended outcome, dependencies/order, sources of truth, invariants, ownership/write boundaries, preserved behavior, and final proof. Distinguish local drafts from live issues. Start with the coherent goal before isolated wording edits.

## Review cross-task consistency

Look for conflicting authorities, overlapping writes, duplicated/missing ownership, assumptions that a prerequisite already happened, incompatible AC, hidden cleanup scope, missing recovery paths, and a final gate unable to prove the integrated outcome.

Check each spec criterion has an owning task and relevant final verification. A scheduling task and a database-only cancellation task do not establish a cancellation guarantee unless the delivery consumer is covered.

Read relevant code and evidence where available. If only documents are supplied, label document contradictions confirmed and runtime claims unconfirmable; do not report executed checks or verified behavior.

## Patch the affected package

When fixes are authorized, update every affected spec/issue rather than only the file where the finding appeared. Keep boundaries executable, verification explicit, and exclusions honest. Mark local patches awaiting GitHub synchronization.

Known CRITICAL/HIGH contradictions that invalidate the package must be resolved or re-scoped before dependent implementation. Changing approved behavior requires an owner decision. Readiness is not acceptance of implementation.

## Re-review changed seams

Record finding IDs, revisions, patches, results, introduced contradictions, open decisions, and evidence limits for each round. Recheck affected contracts and relevant siblings; avoid repeating unchanged material without a reason.

Readiness requires:

- No unresolved CRITICAL/HIGH package contradictions.
- MEDIUM risks fixed or explicitly accepted by the owner.
- Stable dependencies and clear ownership.
- Tasks can start without guessing required decisions.
- Concrete verification and final integration proof where needed.

## Bound the loop

Choose a review budget proportionate to risk; two patch/review rounds are a useful default for an initial pass. If blockers remain at the limit, report their required decision/proof and stop dependent implementation. A budget limit does not downgrade severity or imply a clean package.

New independent features/refactors become follow-up issues when authorized. Do not expand the frozen package to achieve theoretical zero risk. Review-only requests end with findings; do not silently enter a patch loop.

## Execution handoff

When ready, return the dependency-based execution order and ownership. Foundation tasks first, connected changes sequentially where they share contracts/write surfaces, and the final integration gate last. Parallel agents are optional.

The final gate proves integrated behavior in the agreed environment. Individual task PASS records do not substitute for that proof. Keep verified issues open until explicit acceptance and delivery conditions are met.

## Output contract

1. Package verdict, frozen artifact list, and evidence limits.
2. Full severity-ordered findings with IDs, evidence status/class, affected tasks/AC, and scoped package-wide action.
3. Execution order/ownership and any blocking prerequisites.
4. Readiness gates and the next decision/proof for unresolved findings; include round status when fixes were authorized.

Do not claim readiness while known blocking contradictions remain. The review itself neither authorizes execution nor mutates GitHub.
