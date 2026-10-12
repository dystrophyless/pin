# Example: from task-capture requirements to a specification

Hypothetical Pin planning example. It is not an approved requirement, specification, or published issue package.

## Business requirement

User: a person who describes a commitment in Telegram and wants to remember it without manually maintaining a planner.

Problem: free-form messages may omit a date, time, or reminder preference. Saving an invented interpretation can create an incorrect reminder.

Desired outcome: capture the intended commitment, resolve required ambiguity, persist the accepted plan, and confirm the actual reminder schedule.

Success signal: a user can compare the confirmation with the commitment they intended. A numerical quality target needs evaluation data and an owner decision; this example does not invent a baseline.

Out of scope: payment, external calendars, habit analytics, and the complete recurring-series lifecycle.

## Specification

The specification makes the behavioral boundaries explicit:

- Parse the user's proposed task into the agreed input contract.
- Distinguish missing fields from model-proposed values.
- Resolve required date/time-zone ambiguity according to the approved policy.
- Offer untimed tasks when the accepted flow permits them.
- Apply the accepted user-confirmation/save policy.
- Validate before persisting and return a confirmation matching persisted data.
- Define duplicate-input, partial-failure, and retry behavior.

Blocking decisions include the save/confirmation policy, relative-date semantics, reminder defaults, and the persistence/scheduling consistency boundary. The specification stays draft until its required decisions are resolved.

## Issues and verification

Potential boundaries, after the actual contracts are approved:

1. Resolve task input into a validated draft with explicit ambiguity.
2. Persist an authorized draft and agreed reminder schedule without duplicates.
3. Verify the integrated capture flow, including incomplete inputs and failure paths.

Split only where the intermediate capability has a clear standalone contract. Assign issue numbers only after actual issue creation. Issue bodies own dependencies and progress; the spec maps behavior rules to the corresponding issues.

## Traceability example

| User outcome | Spec rule | Issue outcome | Required evidence |
|---|---|---|---|
| The saved plan matches the intended commitment | Required ambiguity is resolved before mutation | Validated task draft | Ambiguous/missing-input scenarios |
| The confirmation shows the actual schedule | Confirm persisted values | Persisted task and schedule | Persistence/response consistency check |
| Retries do not duplicate the plan | Defined request identity and idempotency | Duplicate handling | Repeated-input and injected-failure checks |

These are proposed evidence requirements, not executed results.
