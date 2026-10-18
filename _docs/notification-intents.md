# Notification schedule projection and intent reconciliation

Notifications has two responsibilities in this increment: a pure quiet-hours projection used by Planning previews, and durable, versioned reminder intentions reconciled from committed Planning state. The projection performs no writes. A preview producer displays and approves its exact scheduled instants, omissions, deferrals, and explicit quiet-hour conflicts; Planning stores that approved result with its occurrence. Event processing never creates an intention from preview data or trusts an event payload as current plan state.

## Published contract

`pin.contexts.notifications.api` publishes the immutable `ReminderCandidate`, `OmittedReminder`, `ReminderNotice`, and `ReminderProjection` values and `project_reminders(...)`. Planning resolves calendar-day and elapsed offsets, including past-default omission and past-explicit clarification. Notifications then applies quiet-hour behavior in the selected profile zone:

- A default due inside quiet hours moves to the next local quiet-hour end only when the new instant is after the current time and before the event starts.
- A default that would be useless after deferral is returned as an omission with reason `quiet_unusable`.
- An explicit time stays at the requested instant and returns a `quiet_conflict` notice for the preview.
- Non-conflicting scheduled candidates and Planning's existing omissions pass through with their source and offset provenance intact.

The resulting projection is the one Planning must bind into its existing one-off or series approval digest and persist. Calendar/DST resolution remains Planning-owned; this module accepts already resolved due instants and contains no Planning imports.

## Current-state read and reconciliation

Notifications publishes the `CurrentScheduleStore.lock_current_schedule(change)` consumer port. Planning supplies its implementation through `SqlAlchemyReminderScheduleStore`, wrapping Planning's native locked snapshot reader. It returns a current, owner-scoped `CurrentPlanSchedule` while locking the relevant Planning, occurrence, series, and preference versions in the caller's Unit of Work. It includes every current materialized occurrence, its terminal/open state, version, committed reminder candidates, series state/version, plan version, current preference version, and the preference version that approved that occurrence's reminder rules.

`reconcile_schedule_change(...)` accepts supported one-off and series schedule events, reads this current snapshot, validates that the event identifies the occurrence or series and is not ahead of the current version, then asks Notifications to reconcile it. Older/replayed events may cause the latest locked snapshot to be reconciled; the event payload is never used to overwrite newer state. A missing/mismatched owner, missing target, or future event version gets a durable stale receipt and no intent writes.

`SqlAlchemyNotificationsStore` writes on the caller's `AsyncSession`; it does not commit. It creates an owner/event receipt and the intent changes in the same transaction. Semantic uniqueness is `(owner_id, occurrence_id, offset_kind, offset_amount)`. The row retains the current plan, occurrence, preference, and optional series versions, source event, source offset, and `dispatch_version`. A due-time, source, version, or eligibility change increments `dispatch_version`; invalidation does too, fencing older delivery work.

Only open occurrences with a non-null approved preference version equal to the current preference version can own scheduled intents. Legacy occurrences with no approved version fail closed. Current terminal occurrences, no-longer-approved offsets, and obsolete materialized keys invalidate their pending intent rows. Stopping a series prevents future generation but preserves already materialized open occurrences and their approved schedules. Each occurrence key is independent so completion/history for one repeat does not rewrite another occurrence's intent.

A confirmed settings edit that changes no scheduling fields carries only the exact previously current approval forward atomically; language changes and no-op confirmations retain settings-version fencing without invalidating unchanged schedules. Already stale approvals remain stale. Series metadata-only advancement updates its stored fence without rearming the dispatch. Delivery independently validates the current owned occurrence and exact approved reminder semantics, allowing unchanged reminders to remain eligible before the generation event is consumed. See [focused version-coordination proof](evidence/preferences-reminder-fence-20261018.md).

`SqlAlchemyNotificationScheduleEventConsumer.consume_pending(...)` is the bounded durable event-consumer path. It selects supported Planning schedule events that lack this consumer's owner/event receipt, without using or changing the outbox transport's `published_at` marker. It locks selected rows with `SKIP LOCKED`; unsupported events cannot block this queue. Its snapshot bridge and intent store use the same caller-owned SQLAlchemy session, so a committed event receipt and all intent changes commit atomically. A rollback leaves the event eligible for retry. The consumer deliberately does not use the platform aggregate cursor: it handles only schedule event kinds, while current-snapshot locking and the per-event receipt make replays converge to current confirmed state without requiring consumption of unrelated aggregate events.

The local application worker calls `process_pending_reminder_schedules(...)` in a fresh bounded Unit of Work on every loop iteration, including idle iterations, before it claims another job. This reuses the existing worker lifecycle without adding a scheduler or a second lease system. External delivery remains a separate consumer responsibility.

Omitted reminders are preview evidence, not deliverable rows. This increment does not perform external sends or establish delivery-attempt behavior; those consumers must check the published intent version and current eligibility before dispatch.

## Persistence

Migration `notification_intents_0001` follows Planning's reminder-policy version migration. It creates `notifications.reminder_intents` and `notifications.reconciliation_receipts`, tenant-safe references to Planning ownership rows, unique semantic/event keys, version and state checks, and due/owner indexes. Migration metadata is registered through the repository's single shared Alembic Base.

The migration and reconciler are exercised only against an explicitly supplied, guarded disposable local PostgreSQL database using `PIN_TEST_NOTIFICATIONS_DATABASE_URL`; tests refuse other database prefixes, ports, users, or server data directories.
