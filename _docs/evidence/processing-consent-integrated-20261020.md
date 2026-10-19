# Processing-consent local acceptance — 2026-10-20

## Verdict and source boundary

The Orchestrator accepts the local implementation and integration of the reviewed processing-consent package (#29, #30 and #31) under the delegated rule-based acceptance workflow. No independent implementation Reviewer or personal human review is claimed. Product source is still an uncommitted working tree; this is not deployment, hosted verification, commercial/legal certification or final bot-phase owner acceptance. The issues remain open for delivery reconciliation.

The final source manifest and raw JUnit, stdout, invocation, network-fence and cleanup records are retained privately. Each accepted run had identical source before/after, zero skipped cases, and no attempted non-loopback Python networking. Earlier passing packets were compared with final source: the later changes were the separately verified recovery CLI async runner, test-only fixture/assertion corrections, and onboarding/privacy newline formatting. Final UI/worker composition was rerun after those formatting changes. Earlier failures are retained and are not relabelled as passes.

Owner amendment v0.21 removes jurisdiction-specific financial-retention/entity/tax validation from acceptance and closure requirements: WAIVED / NOT ASSESSED. This is not a legal PASS and does not change payment-record retention behavior. Terms of Use, Privacy Policy, explicit consent, support/payment transparency and functional deletion/recovery remain required.

## Verified checks

| Check | Passing cases | Evidence type |
|---|---:|---|
| Identity/onboarding | 12 | Native PostgreSQL, final presentation source |
| Privacy/payment support routes | 2 | Native PostgreSQL, final application source |
| Consent admission, fencing and recovery | 14 | Native PostgreSQL |
| Capture and actual Save/Confirm callbacks | 8 | Native PostgreSQL |
| Delivery, withdrawal and deletion | 34 | Native PostgreSQL |
| Voice worker and transcript continuation | 17 | Native PostgreSQL |
| Trusted ingress | 12 | Native PostgreSQL |
| Reply preparation | 9 | Native PostgreSQL |
| Deletion, maintenance and tombstone replay | 7 | Native PostgreSQL |
| Provider usage/replay | 5 | Native PostgreSQL |
| Actual dump/restore scenarios | 2 | Separate disposable source/target rehearsals |
| New RU/EN consent journey | 4 | Native persisted ingress/application/delivery composition |
| Existing RU/EN core lifecycle | 4 | Native persisted composition with explicit consent |
| Recurring lifecycle | 1 | Native persisted composition with explicit consent |
| Paid access and quotas | 4 | Native persisted composition, synthetic payment events |
| Model adapter | 53 | Offline unit checks |
| Capture | 18 | Offline unit checks |
| Recovery state | 10 | Offline unit checks |
| Restore shell contract | 17 | Offline shell checks, not actual restore proof |
| Consent/privacy presentation | 8 | Offline unit checks after newline correction |

Final scoped Ruff passed for 57 changed/new Python files; full source mypy passed for 222 files; architecture import boundaries and git diff whitespace checks passed. These counts describe the listed modules and compatible packets, not a fresh full-repository suite or model-quality benchmark.

## Acceptance mapping

| Requirement | Concrete proof |
|---|---|
| #29 Identity controls; PC-01/PC-08 | Explicit localized onboarding callback, current policy/version, owner-bound single-use controls, Details naming OpenRouter, grant/revoke/regrant revisions, rollback of consent/profile/trial/token effects on late failure; 12 native Identity cases and final RU/EN journeys. Initial notice stays vendor-neutral. |
| #30 AC-01; PC-02 | Trusted admission binds immutable grant provenance; duplicate/legacy input cannot acquire a later grant; old profiles receive denial/resend without model calls or quota use. Real allowance denial covers revoked, missing-row and absent-owner cases despite active Billing access. Unconsented voice has no download/transcription/AI reservation. |
| #30 AC-02; PC-03 | Withdrawal between first provider response and repair prevents the next attempt; the started receipt/cost survives independently from user settlement/refund. Configured provider/speech/direct compositions retain authorization; model-native replay and stale-response cost cases pass. |
| #30 AC-03; PC-04 | Native post-authorization/precommit barriers serialize provider-start, result/proof, actual Save/Confirm and AI reply markers with withdrawal. The inverse actual Save callback and a newly admitted retry after regrant cannot consume the old proof or write plans. Identity late-failure rollback and missing authorization fail closed. Direct deletion/maintenance/replay and ordinary reminder/recovery-summary races use bounded completion and native blocker observations. |
| #30 AC-04; PC-04 | Queued, transcript, cached-result, proof and reply provenance is retained; stale results terminalize and cannot be revived by regrant. Speech cancellation/failure cleanup and existing receipt/lease behavior remain covered. Withdrawal can commit while a fake provider call is paused: inference holds no authorization transaction. |
| #30 AC-05; PC-05 | Without a current grant the final RU/EN journey exercises help, privacy, support, today/week, subscription, terms, payment support, cancel and settings with no extra provider call or usage reservation. Native ordinary delivery and synthetic paid flows retain their existing behavior. No inference of payment authorization from processing consent. |
| #30 AC-06; PC-06 | Delete/return and tombstone replay remove consent/artifacts while preserving approved trial/current-paid facts and later legitimate generations. A fresh grant is required; old owner/grant artifacts remain unauthorized. |
| #30 AC-07; PC-07 | Existing-profile migration does not backfill a grant. Exact-generation restored-grant invalidation, idempotent retry and missing-evidence/failure fencing pass; both actual local restores retain deletion/delivery/payment/usage prerequisites. The runbook requires guarded workers, stopped old workers before migration, and fenced rollback to an older worker. No rollout/rollback was executed for this increment. |
| #31 integrated gate | Actual RU/EN persisted onboarding/legacy denial, Details, acceptance, new request, provider receipt, Capture, reply and deterministic post-withdrawal paths pass. Exactly one new recipient/body match and one confirmed attempt are tied to the exact owner-bound reply event/source job. Predecessor native races/deletion/restore supply the remaining PC proofs. |

## Corrections and evidence limits

- The new integrated fixture originally counted all consent/onboarding replies as AI sends. It now correlates the new request and exact event/attempt, preserving strict content and recipient checks.
- Its frozen business clock could precede the database-created reply timestamp after lengthy onboarding. The helper now advances only the delivery boundary to at least that event timestamp, matching the real due predicate; production scheduling was unchanged.
- A Capture test reused an already-closed session and self-blocked trigger cleanup. Root stopped only the verified owned pytest child; the corrected test closes transactions before DDL cleanup. No PostgreSQL restart or forced session termination was used.
- The delivery test file was semantically reconstructed after a write-encoding error; it is not byte-identical recovery. All 18 original test functions were preserved and the complete resulting 34-case module passed. The new native liveness proof retains one-send, tombstone, exact lost-lease and no-resurrection assertions.
- Already-started provider or Telegram attempts may complete after withdrawal. A purge can remove a claimed job before its outcome transaction; the worker reports LeaseLost rather than recreating deleted state. This does not authorize a new attempt or replay.
- All manifest-owned fixtures were removed with ordinary DROP and exact protected catalog comparison. Python socket guards do not attest to arbitrary non-Python/native networking.
- External service boundaries are synthetic. No new OpenRouter call, real Stars payment, actual Telegram client check, hosted restore, deployment, 99% corpus or capacity/economics benchmark was performed for this increment.

## Remaining delivery

Commit/push authorization for this product increment, its resulting CI/deployment evidence, the remaining bot-phase delivery reconciliation and concrete final owner acceptance are separate. The financial legal-validation waiver is already recorded; it is no longer a blocker. Local Terms/Privacy/consent readiness must not be presented as already deployed behavior.
