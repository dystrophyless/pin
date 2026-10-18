# Runtime regression compatibility — 2026-10-18

Root compared the complete native-qualification snapshot with the deployed runtime release using `git diff --name-only` restricted to source, tests, migrations and deployment helpers. The earlier 602-case result remains a result for its frozen snapshot; this comparison does not relabel it as a fresh full-suite run.

The complete changed runtime paths are the Telegram application worker, notification delivery worker and platform jobs. The affected native delivery/reply modules passed 41 cases after policy-deferral changes. The application-worker help/support correction passed 14 presentation/persisted-support cases; its later import-only cleanup passed Ruff and scoped mypy. The runtime release passed Linux CI and deployment, and its host-health service passed after the isolated recovery rehearsal.

Deployment-helper changes are limited to executable backup permissions and the writable private scratch mount for ledger replay. Seventeen focused shell cases passed; actual hosted restore/reconciliation/release/check/cleanup subsequently completed. Changed tests are the corresponding delivery, reply, support and restore-shell modules. No migration changed between these two snapshots. All other tracked source/test/deployment paths in that comparison are unchanged.

This is a compatibility argument combining unchanged-source regression evidence with focused checks of changed paths. It supports assessing existing results without unnecessary identical reruns. It does not replace missing real Telegram integration, provider eligibility, statutory financial-retention evidence or final acceptance. See [hosted recovery](hosted-recovery-20261018.md), [operations/support](operations-acceptance-20261018.md), and the [acceptance matrix](final-acceptance-matrix-20261018.md).


## Later focused corrections

The opening comparison above is historical; it does not describe every subsequent runtime change. Later source changes have their own bounded checks and actual client observations:

- Capture presentation/model prompt corrections and deterministic named-task selectors: [dialogue](telegram-dialogue-20261018.md), including no-write-before-save, command replay and explicit cleanup observations.
- Weekly future-pattern projection and localized reminder labels: [19 native mutation and 17 presentation cases](weekly-reschedule-20261018.md), plus the actual confirmed Wednesday move and persisted intent reconciliation.
- Normal/outage accountability composition: [four RU/EN cases](core-recovery-checkin-20261018.md), test-only composition with controlled clocks and synthetic transport.
- Neutral preference approval, series metadata reconciliation and canonical delivery fencing: [47 native cases](preferences-reminder-fence-20261018.md), plus the actual post-language-edit reminder delivered once during quiet hours on 875222a.
- Advice event-local input serialization: [69 root-run adapter/Capture cases](advice-temporal-context-20261018.md) on 9091d7b. Deployment passed; the provider retry still failed temporal grounding. Subsequent prompt-only ad13c12 passed another 69 root-run adapter/Capture cases and deployed with exact host health, but its actual advice still invented purpose/recurrence and confused event/reminder time after clarification.

No new migration or output proposal contract is introduced by these fixes. These per-seam checks and compatible unchanged-source observations support the numbered [core inventory](core-scenario-coverage-20261018.md); they are not a new 602-case or combined same-clock full-suite result. Each source revision, test transport and actual-client boundary stays attached to its evidence. The latest advice finding prevents a complete core verdict until its bounded retest succeeds.
