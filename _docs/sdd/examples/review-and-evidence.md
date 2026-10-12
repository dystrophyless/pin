# Examples: review findings and verification evidence

Hypothetical Pin review scenarios. They do not describe existing implementation bugs or executed test results.

## Cancellation API versus delivery truth

Specification claim: cancelling a pending reminder prevents later delivery attempts within the accepted boundary.

Issue A proposes caching pending reminders in the worker. Issue B changes database status but excludes worker signalling or a delivery-time cancellation check. The final gate checks only that the API returns cancelled.

Review result:

- HIGH, confirmed task-doc gap: the package provides no consumer-side mechanism supporting its claimed cancellation guarantee.
- HIGH, confirmed gate gap: an API response does not prove suppression of a cached delivery attempt.
- Runtime bug status: unconfirmable without implementation and execution evidence.

Next proof: a controlled scenario where the worker selects a reminder, cancellation completes, and the agreed boundary determines whether the delivery adapter may be called.

## Duplicate task creation after a partial failure

Specification claim: retrying the same canonical input does not create duplicate work.

A task covers a successful create request but leaves the behavior after persistence succeeds and the confirmation fails unspecified.

Review result:

- Confirmed document gap: the retry identity and partial-failure contract are missing.
- Runtime duplicate risk: partial or unconfirmable until the actual producer/consumer and persistence path are inspected.

Next decision/proof: define canonical request identity, then verify a retry after the specified partial failure. Do not choose product policy silently during review.

## Evidence strength

| Evidence | What it can establish | What still needs separate proof |
|---|---|---|
| Unit test of date conversion | Behavior for controlled input and clock | Telegram interaction and persisted schedule |
| API/persistence check | Stored result and response consistency | Worker delivery behavior |
| Controlled worker scenario | Delivery attempts in a defined environment | Real Telegram delivery and deployed recovery |
| Hosted smoke | Exercised deployed path at a specific revision | Unexercised failure/recurrence cases |

## Finding and recheck record

Use stable finding IDs and record severity, evidence status, finding class, affected AC, exact references, and next action. After a fix, rerun the original reproduction and relevant sibling scenarios. Keep not run, blocked, and remaining limitations explicit.

Use the [issue evidence templates](../templates/issue-evidence.md). Verification changes the task to verified when authorized; acceptance and closure require the agreed owner decision and delivery conditions.
