# Business requirements guide

Use the [BR template](../templates/business-requirements.md) to define a product requirement.

## Purpose

A BR explains the user's problem, expected change, and evidence of success. It gives a spec a product basis without prematurely prescribing every implementation detail.

## Write the requirement

1. Describe the user, situation, and job to be done. State the friction concretely.
2. Describe current behavior or workarounds and their shortcomings. Cite research or existing product behavior; distinguish evidence from hypotheses.
3. Define the proposed outcome and the smallest useful increment.
4. Describe the behavioral user flow, including important alternative or failure paths. Detailed UI geometry belongs in design references, not generic BR prose.
5. Define success signals. For each metric, distinguish a measured baseline, a proposed target, and an unvalidated estimate.
6. List constraints, non-goals, rollout considerations, and support needs when relevant.
7. Record confirmed decisions, assumptions, and open questions separately. Assign a decision owner or identify the information needed to resolve a blocker.

## Pin-specific application

The [existing brainstorm](../../PROJECT_BRAINSTORM.md) is an input. Preserve its confirmed choices, such as Telegram-first interaction and application-owned reminders. Do not promote provisional hosting, prices, payment details, confirmation policies, or recurrence semantics into approved requirements.

English is the documentation language. The product's Russian/English user experience follows its own requirements.

## Ready for specification

- The user and problem are identifiable.
- The desired observable outcome is clear.
- Scope and exclusions are understandable.
- Relevant evidence is cited and claims are qualified.
- Blocking questions are resolved or explicitly prevent dependent implementation.
- Approval status and approved revision are recorded. A draft's existence is not approval.

For a narrow bug or maintenance change, an issue can state the problem directly; a separate BR is unnecessary when it would only duplicate existing approved context.
