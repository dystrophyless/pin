# Issue #8: Planning candidate verification

Status: **in progress; not accepted**. Date: 2026-10-13 (Asia/Qyzylorda).

Candidate implementation: `dd434aaf2a08c90e4e9213bed1449c6c11236a90`, including compatible Identity/Billing freshness changes at `0d7b6738e079e79968a9f986498d2efc1bc2797d`. These are local commits, not pushed.

Parent exported committed source to `C:/Users/roman/AppData/Local/Temp/pin-parent-plans-91ca/source`; import provenance confirmed that snapshot. The locked CPython 3.12.12 environment and guarded local PostgreSQL 18.6 cluster at loopback 51017 were used. Nine fresh isolated databases covered the existing integration suites plus Planning; historical provider-accounting records were untouched.

Full opt-in suite: **169 passed, no skips, 60.30 seconds** (chunk `895484`). Ruff passed, mypy passed on 85 source files, and architecture passed. Planning's 12 PostgreSQL and eight domain-unit cases are included. They cover atomic batch/receipt/outbox writes, concurrent duplicate and conflicting replay, owner/version/proof rejection, persisted same-UoW proof consumption with rollback/retry, write-boundary access expiry and preference changes, event becoming past, temporal DST semantics and stable reschedule identity. The actual preview producer remains future capture/mutations work; the proof issuer used here is test-only persisted infrastructure.

Alembic `upgrade head`, `current`, `check`, `downgrade base`, `upgrade head`, `check` passed for the exact source, with head `planning_one_off_0001` and no metadata drift (chunks `b01436`, `3421bc`). All nine newly parent-owned databases were dropped and their absence verified after the checks; the proof snapshot/runners remain available.

## Remaining acceptance gap

Parent inspection found that the proof fingerprint binds typed fields and preference version, but not the effective reminder projection. A future default reminder can become past while the event remains future; reprojecting at application silently omits that formerly approved reminder. SPEC-001 section 11 requires a notice and approval of the displayed remaining schedule. The Planning executor is adding a deterministic approved-projection binding, omission metadata and persisted negative/reapproval fixtures. The current green suite does not establish that requirement, so #8 remains open/in progress and downstream work is not yet unblocked.

Final follow-on source, verification and acceptance readback will replace this candidate checkpoint. No natural-language capture, Telegram saved-plan dialogue, recurring generation, notification delivery, provider call, Docker, deployment or overall MVP acceptance is claimed.
