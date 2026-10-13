# Issue #8: accepted local Planning foundation

Date: 2026-10-13 (Asia/Qyzylorda). Direct Orchestrator acceptance under the owner's standing implementation/closure authority; GitHub publication/readback follows this checkpoint.

Verified source: `65c40df6b4edb69b7893021a3ed7100738136d92`, including one-off implementation `dd434aa` and Identity/Billing freshness guards `0d7b673`. Local commits are unpushed. Approved source: SPEC-001 section 11, current v0.5.

The parent exported committed source to `C:/Users/roman/AppData/Local/Temp/pin-parent-projection-a36f/source`; import provenance confirmed that snapshot. Environment: locked CPython 3.12.12 on Windows, local PostgreSQL 18.6 on loopback 51017, guarded disposable cluster. Nine newly created isolated fixture databases covered all opt-in suites; historical provider-accounting records were untouched.

## Acceptance evidence

- Full PostgreSQL-enabled suite: **172 passed, no skips, 54.46 seconds** (chunk `bd2603`). Ruff passed; mypy passed on 86 source files; architecture boundaries passed (chunk `a69626`). Raw command logs: `check-0.log` through `check-4.log` in the proof directory above.
- Planning: nine domain-unit and fourteen PostgreSQL cases. Typed temporal validation covers appointment duration/end, deadline instant, genuinely untimed date, event zones, DST calendar-day versus elapsed offsets, gaps and explicit repeated-hour choices. Owner/version/access/proof checks, mixed invalid batches, atomic plan/rule/receipt/outbox rollback, concurrent duplicates, conflicting replay, persisted proof consumption rollback/retry and stable reschedule identity are verified.
- Public `preview_plan_batch` fingerprints exact typed payload/preferences version, resolved local/UTC schedules, scheduled reminders and omitted default reminder offsets/provenance/instants/reasons. Generated IDs and processing time are excluded. The trusted producer must bind that fingerprint; there is no permissive fallback. The command refreshes locked Identity preferences and current Billing access, then recomputes at the write boundary. A reminder becoming past before application or during paused proof verification rejects obsolete approval with zero committed fragments and unspent proof. Fresh approval of the remaining schedule succeeds and preserves omission notices in the receipt and outbox. Exact receipt replay after time passes returns the original result without duplicate events.
- Alembic upgrade/head/current/check, downgrade/base, re-upgrade/head/check passed against this exact snapshot; head `planning_one_off_0001`, no metadata drift (chunks `9f0161`, `2d7848`, `8d740e`). No schema amendment was needed for the approval fix.
- All nine parent-owned fixture databases were dropped after verification; exact names and cluster identity were guarded and absence checked. Proof runners/logs remain outside the repository.

## Limits

This accepts the typed one-off Planning foundation, not natural-language capture or a live saved-plan Telegram dialogue. The persisted preview/proof producer in these tests is test-only; capture/mutations must implement the production producer and render the published exact projection. Success data is available to the caller after its shared UoW commits; Telegram delivery remains later work. Recurrence, notification intent generation/delivery, actual deletion coordination, provider calls, Docker/deployment and overall MVP/release acceptance are not claimed. The private reschedule primitive is storage evidence only; issue #15 owns authorized user mutations.

The earlier 169-pass candidate checkpoint did not prove the reminder projection requirement. The follow-on tests and final source above close that specific gap; no prior green result is substituted for it.
