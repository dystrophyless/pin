# Issue evidence and acceptance comment templates

Use only the relevant section. Replace instructional fields before posting; do not post a template as completed evidence. Publishing comments requires authorization.

## Implementation checkpoint

```text
Implementation checkpoint — YYYY-MM-DD HH:MM TIME_ZONE
Issue / spec:
Revision or dirty-worktree boundary:
Changed behavior and files:
Completed scope:
Remaining scope / blocker:
PR or local diff:
Next action:
```

## Verification record

```text
Verification — YYYY-MM-DD HH:MM TIME_ZONE
Issue / spec revision:
Code revision or dirty-worktree boundary:
Environment and test data isolation:

AC ID | Command/scenario | Expected | Actual | PASS/FAIL/NOT RUN/BLOCKED | Evidence

Unverified surfaces:
Known findings and severity:
Delivery/deployment state:
Workflow status: verified, or actual incomplete/blocked state
```

A local unit run is not browser proof, deployment proof, or production evidence. If logs/screenshots cannot be attached publicly, use an approved evidence location and omit secrets or private user data.

## Review finding and fix verification

```text
Finding ID and severity:
Class: product bug / task-doc gap / architectural seam / decomposition problem
Evidence status: confirmed / refuted / partial / unconfirmable
Claim and relevant code/doc reference:
Reproduction or proof:
AC / contract affected:
Recommended scoped action:
Owner / follow-up issue:

Fix verification:
Fixed revision:
Original reproduction result:
Relevant sibling/negative checks:
Remaining limitations:
```

## Acceptance record

```text
Acceptance — YYYY-MM-DD
Accepted by:
Authorization source: direct user instruction or owner approval URL
Issue / spec revision accepted:
Implementation revision and evidence accepted:
Delivery conditions and whether they are met:
Residual risks explicitly accepted:
Follow-up issues:
Closure: accepted and closed, or acceptance recorded while awaiting delivery
```

Do not fill this record on the owner's behalf without actual acceptance. A test result or merged PR is not an acceptance source.

## Blocker / cancellation record

```text
Status: blocked or cancelled
Reason and evidence:
Previous workflow status:
Decision owner / missing prerequisite:
Resume condition, if blocked:
Cancellation authorization, if cancelled:
Follow-up issue, if any:
```
