# Text capture and confirmation

The Conversation capture flow stores an owner-scoped draft before calling the model. The worker
must split each model-backed turn into three phases:

1. In a short caller transaction, call `prepare_interpretation` and commit its per-user sequence,
   input receipt, processing fence, and bounded conversation context.
2. Outside any database transaction, call the existing `BoundedPlanningAssistant.interpret` with
   the returned context. The assistant owns allowance reservation, provider attempt facts, result
   replay, and settlement; capture must not reserve a second AI turn.
3. In a new caller transaction, re-check the current preference snapshot and call
   `finish_interpretation` to persist either clarification, failure, or the complete unsaved
   preview plus exact-projection proofs.

The returned `CaptureTurnReply.response` is the durable rendering source. A `preview` response
contains the full one-off and recurring projection, exact reminder instants and omissions, policy
notices, and `proofs`; its confirmation credential is only returned for the callback button and is
stored hashed. No plan or series is written in this phase. `confirm_ready_draft` re-reads the
owner's preferences and access, rebuilds the exact preview, consumes the one-use proof, and creates
the complete batch in the caller's transaction. The caller must roll back that transaction if any
item fails. `cancel_draft` performs deterministic draft cancellation only.

The guarded PostgreSQL capture tests exercise an injected typed model result through preview and
confirmation: a mixed one-off plus recurring batch stays unsaved until confirmation, survives
restart, and concurrent confirmation/replay creates one committed set of plans, series, events,
proof consumption, and receipts. They compare canonical reminder instants in the preview with
Planning reminder rules and Notifications intents after event reconciliation, and verify that
stale, expired, wrong-owner, changed-preference, and expired-access confirmations write nothing.
A simulated failure after the recurring half has flushed also proves the entire mixed batch and
proof consumption roll back together.

An incomplete typed proposal is persisted in `CaptureDraft.normalized_actions` while the draft is
collecting. A clarification response may include `choices`, each with a server-declared `id` and
localized `label`. Bind each choice callback to action `capture.choice:<id>`, the authenticated
owner, the draft UUID, the current draft version, and the 24-hour expiry. Consume that callback
token in the same transaction as `apply_clarification_choice`; the function also reloads the
owner-filtered draft and rejects any choice absent from its current clarification. Choice tokens
use the `capq:` Telegram callback prefix, cancellation uses `capx:`, and confirmation uses `capc:`;
all callback data must remain within Telegram's 64-byte limit.

Selecting a timed-task suggestion, untimed option, or example appointment duration updates only
the collecting draft and then produces a new ready preview. A suggested time comes from the
owner's configured review time and appears only when its resolved event instant is still in the
future. It is labeled as a suggestion, not availability. Appointment duration examples are
choices, not defaults. “Choose another time” and “enter exact duration or end time” remain
collecting and ask for text; the subsequent natural-language correction is a model-backed turn.
Buttons do not consume AI allowance.

The model batch schema accepts only create proposals and carries `schema_version=1`. Capture may
prepare at most 10 items in one atomic batch; this is an engineering bound, not a separate product
limit. A capture assistant can request up to 4096 output tokens so a complete ten-item typed batch
fits; the assistant default and offline/historical smoke bound remain 512 tokens. Live model
quality for ten-item batches has not been qualified. If a response is explicitly truncated or a
provider returns more than 10 items, capture asks the user to split the request or clarify and
saves no plans. Reminder `null`/omission keeps profile defaults,
an explicit empty list clears reminders, `override` replaces defaults of matching kinds, and `only`
replaces the full effective list. Recurring reminders must be relative to each occurrence.

When a user corrects an unsaved multi-item batch, the model context retains the pending normalized
items while the old preview and proofs are invalidated. The model must return a complete replacement
batch preserving unchanged items and previously clarified values. A single-item answer cannot
shrink a pending multi-item batch; if the complete replacement is unclear, the draft remains
collecting and asks for clarification without saving a preview or plan.

Use `PIN_TEST_CAPTURE_DATABASE_URL` only for the assigned disposable `pin_issue14_` PostgreSQL
database. The integration fixture checks both that database prefix and the exact approved server
data directory before running Alembic. It never drops or resets the database.
