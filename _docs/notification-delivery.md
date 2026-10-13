# Notification delivery

The delivery worker reuses `platform.jobs` on the `delivery` lane. Its bounded
pumps only enqueue scheduled reminder intents whose effective due time has
arrived and private `telegram.reply.requested` outbox envelopes not already
represented by an attempt or durable job. Reminder deduplication keys include
the intent dispatch version; reply keys include the source event ID.

Before a reminder send, the consumer holds one fresh transaction, fences the
live job lease, locks the reminder intent, and reads the current Planning
schedule, Identity preferences, Billing access, private Telegram owner
mapping, and current agenda title. It compares the plan, occurrence, series,
preference, and dispatch versions and checks that the occurrence remains open.
Stopped series generation does not cancel open occurrence reminders. Active
access is checked independently from chat quota. Expired access produces a
visible skipped outcome.

Default reminders inside quiet hours are deferred only when the next quiet-hour
boundary remains before the event start. The original schedule due time stays
unchanged; `not_before_at` carries the deferral and the dispatch version fences
the resulting delivery. Explicit reminders retain priority and record a
`quiet_conflict` on their attempt outcome. Late delivery considers other due
reminders for the same occurrence and coalesces an older one when a newer
eligible reminder is available. Advance reminders are skipped after event
start.

The consumer commits a `started` attempt while holding the live lease, closes
the transaction, calls Telegram, then opens a new transaction and fences the
same lease before recording the provider result. A reclaimed job with an
unresolved `started` attempt is finalized as `unknown` and never sent again.
Confirmed and unknown attempts are terminal for that intent/target/version.
This boundary prevents stale leases from authorizing or finalizing a
replacement attempt, but cannot retract a request after the external call has
started; it does not claim exactly-once delivery.

Telegram 429 responses are known rejections with a durable delayed retry;
forbidden destinations become blocked; definitive bad-request rejections are
terminal failures; transport exceptions with an uncertain provider result are
recorded as unknown and never retried or fallback-sent. Private replies share
the lane and outcome ledger without reminder plan, quiet-hour, or entitlement
gates. Before sending a reply the adapter resolves the current owner mapping
for this bot and requires it to match the persisted private chat target. A
reply's Markdown-to-plain-text fallback is limited to a confirmed formatting
rejection. Billing lifecycle `DELETED` suppresses pending replies, while
`MISSING` remains eligible for first-time onboarding and `PRESENT` remains
eligible for access-status replies even when entitlement has expired.

`notifications.delivery_attempt_outcome` is the consumer-facing receipt. Its
key fields are `intent_id` or `reply_event_id`, `target_id`,
`dispatch_version`, `attempt_id`, `attempt_number`, `outcome`, optional
`check_in_cycle_id` or `recovery_episode_key`, `access_period_id`,
`quiet_conflict`, and a bounded `failure_code`. Accountability owns check-in
cycle creation and timer eligibility; this delivery worker only carries an
optional cycle ID through the receipt. TR-01 is preserved for that consumer:
when recovery occurs after the expected end and no initial check was delivered,
the later cycle producer may create one late check, start its one-hour
follow-up timer from confirmed delivery, and defer that follow-up during quiet
hours. This worker does not create cycles or timers.

`RecoverySummaryRequest(owner_id, episode_key)` is the separate summary
contract. The recovery/renewal coordinator supplies a stable episode key; this
increment does not detect outages or renewal episodes. The existing DELIVERY
job deduplication key and attempt subject are owner-plus-episode scoped, so
concurrent or replayed requests produce at most one confirmed summary for the
episode. Before send, the worker requires the current private owner mapping, a
present owner with active reminder-delivery access, current preferences, and
the quiet-hours window to be open. It reads only current owner-scoped overdue
open plan rows through Planning's bounded `list_overdue` API, including
appointments by end, deadlines by deadline, timed tasks by start, and untimed
items by local date. It sends no message when there are no such rows. The text
summarizes unfinished plan state; it does not create an initial check, a
follow-up, or a second campaign. Recovery summaries are sent as plain Telegram
text so plan titles are not interpreted as Markdown. Unknown delivery remains
terminal for that episode and is never resent automatically.

The opt-in PostgreSQL test suite uses `PIN_TEST_DELIVERY_DATABASE_URL`. It
accepts only the dedicated local `pin_issue12_` database and checks the test
cluster's `SHOW data_directory` before Alembic creates or updates objects.
Its queue microbenchmark uses 20 successive batches of 101 due intent keys:
the first 100 keys in each batch already have completed durable jobs, and the
last key must be pumped and claimed. The measured pump-plus-claim p95 is
recorded separately as `pump_claim_p95_seconds`; it is not dispatch-start
evidence. The actual delivery profile makes 50 independent owner reminders due
before one worker iteration, then records from the start of that eligible
batch through each fault-free injected transport invocation after the full
eligibility/prepare path. The p95 is asserted at or below 10 seconds and
recorded as `actual_transport_dispatch_start_p95_seconds`. It uses no live
Telegram messages and makes no claim about concurrent workers or a deployed
host.
