# Example: cancel one pending reminder

Hypothetical Pin issue draft. The cancellation contract below illustrates task structure; it is not an approved implementation policy or a live GitHub issue.

## Task kind

Product increment

## Workflow status

draft

## Increment

After an authorized user cancels a pending reminder, the application reports the cancelled state and prevents a later delivery attempt according to the accepted cancellation boundary.

## Scope

- Validate ownership and reminder identity.
- Persist the agreed cancellation state.
- Ensure the scheduling/delivery consumer observes cancellation.
- Return a confirmation matching the persisted result.
- Define repeated cancellation behavior.

## Out of scope

Recurring-series cancellation, changes to the parent task, and recall of an already sent Telegram message.

## Dependencies and decisions

- Requires an approved reminder-state and cancellation contract.
- Depends on the actual scheduler/delivery issue once created.
- Owner and priority: unassigned.
- Open decision: the boundary for cancellation racing with an in-flight delivery.
- Open decision: repeated cancellation response semantics.

The task cannot become ready while those behaviors remain undecided.

## Implementation anchors

No reminder implementation exists in the project at this checkpoint. Once it exists, trace the cancellation API, persistence, scheduler selection/cache, and delivery consumer. Updating the database alone may not establish the delivery guarantee.

## Draft acceptance criteria

- [ ] Cancelling a pending reminder results in the agreed persisted and user-visible state.
- [ ] A reminder loaded by a worker before cancellation follows the approved cancellation guarantee.
- [ ] Repeated cancellation follows the accepted idempotency contract.
- [ ] An unauthorized user cannot cancel another user's reminder.
- [ ] A cancellation/delivery race follows the explicit accepted boundary rather than an assumed guarantee.

## Verification plan

Use isolated reminder fixtures and a controlled delivery adapter. Exercise cancellation before worker selection, after selection, on repeated input, and at the defined in-flight boundary. Inspect delivery attempts as well as API responses. Record actual commands, revision, environment, and results when implementation is available.

## Delivery and acceptance

Delivery conditions require an owner decision. Evidence and explicit acceptance belong in the issue. An API response or a passing happy-path test alone does not prove the complete increment.
