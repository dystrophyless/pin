# Preferences and series reminder version coordination — 2026-10-18

The actual Telegram quiet-hours fixture exposed a lost reminder after a language-only preferences edit: the global profile version advanced, but the unchanged saved schedule retained its older approval version. Delivery therefore recorded `obsolete_or_incomplete_schedule`. The fixture was subsequently completed explicitly and its intent invalidated; the failure remains in [dialogue evidence](telegram-dialogue-20261018.md).

## Correction

The application composition keeps the global settings version and stale-edit protections. Only a confirmed edit with identical home zone, reminder defaults, quiet hours and review time carries approval forward from the exact previously current version. This includes changing language or confirming without changes. Already stale approvals, terminal occurrences, invalidated intents and actual scheduling-policy changes are not automatically approved.

Open approved occurrences and their scheduled intents advance atomically with that edit. Active series templates are locked in a consistent order and their exact current approval is carried forward for future materialization; existing generation/version fences remain. Matching materialized intents retain their dispatch identity, and the existing series approval event is published.

The recurrence regression also exposed a pre-existing rearm path: generating a later occurrence advances series metadata, and reconciliation treated that alone as a new dispatch for unchanged existing reminders. Reconciliation now updates that metadata without incrementing dispatch version. Genuine changes to state, due time, source, plan/occurrence/preferences versions or series identity retain the existing dispatch-fencing path.

Delivery can race ahead of the generation event consumer. The send check therefore validates the current owned plan, open occurrence/version, approved/current profile, series identity, and exact reminder offset/time/source against Planning's locked canonical snapshot. A parent series metadata version alone no longer suppresses that otherwise unchanged reminder. This does not remove actual mutation or approval fences.

## Native proof

| Full PostgreSQL module | Raw JUnit result |
|---|---:|
| Notifications reconciliation | 5 passed; 0 failures/errors/skips; 6.475 s |
| Application home-zone/preferences runtime | 10 passed; 0 failures/errors/skips; 19.347 s |
| Notification delivery consumer | 32 passed; 0 failures/errors/skips; 32.674 s |

The composed cases exercise real persisted Settings callbacks, actual Planning/Notifications stores and delivery worker with fake transport. They cover neutral language/no-op edits, unchanged already-confirmed sends, unchanged stale approvals, a true policy-change negative, future series materialization/delivery, and generation followed by due delivery **before** event reconciliation. The latter drains again after reconciliation and asserts one confirmed attempt, one send and unchanged dispatch version.

Root inspected the final production/test diffs and raw JUnit counts, matched all recorded tested-file digests, and independently passed Ruff, production mypy and architecture checks. The affected full consumer module is included above. Earlier console-only runs are not substituted for these final raw results; earlier failed harness outputs remain preserved, including incorrect reply correlation and an initial event-count assumption. The final harness binds each claim/reply to its exact inbox/source job and follows rendered localized callback controls.

Each final native fixture had a pre-create manifest, cluster identity and complete catalog snapshot, owner/session checks before ordinary non-forced cleanup, and complete after snapshots. Root independently queried the actual PostgreSQL cluster and matched its exact original 176 catalog rows including two templates, therefore **174 non-template databases**, with all protected records preserved and every manifested fixture absent. Root's initial audit count mistakenly included templates; correcting that audit assertion required no database mutation or product change.

## Limits

These are focused local PostgreSQL/fake-transport results, not a new full frozen regression, payment transaction or general model-quality result. Release `875222a` passed [CI/deployment 39154707168](https://github.com/dystrophyless/pin/actions/runs/39154707168), including temporary SSH cleanup. Root observed that exact active release and host-health success/exit zero. In the actual retry, a new RU preview showed 23:48 Asia/Almaty with the explicitly prioritized 23:47 reminder and quiet conflict, zero plans before confirmation, then one saved open occurrence with matching version-9 occurrence/intent approvals and dispatch version 1. A confirmed language-only RU-to-EN edit advanced both approvals to version 10 while retaining dispatch version 1, due time and no deferral. The authenticated Telegram client displayed the English reminder at 23:47. A read-only query found exactly one confirmed attempt at 18:47:00 UTC with no failure, approvals still 10 and dispatch still 1. This resolves the observed lost-reminder case for this bounded journey. After the advice follow-ups, root explicitly completed only this fixture through Done plus separate confirmation. The client reported completion; PostgreSQL showed completed/version 2 and intent invalidated/dispatch version 2, with the single prior confirmed attempt retained and no duplicate. The original RU profile and other preferences were restored. Previously stale user schedules are deliberately not silently reapproved; no unrelated plan content is changed by this correction.
