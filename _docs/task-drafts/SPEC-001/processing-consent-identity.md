# Record explicit processing consent through Telegram onboarding and privacy controls

<!-- pin-task: SPEC-001/processing-consent-identity -->

Publication: synchronized with issue #29 (https://github.com/dystrophyless/pin/issues/29) on 2026-10-19 after package READY.

## Task kind

Product increment with a reusable Identity authorization capability.

## Workflow status

ready

## Requirement and specification

[SPEC-001 processing consent](../../specifications/SPEC-001_processing_consent.md), C1/C2/C4 and the Identity/migration/purge portions of C5; PC-01, PC-02, PC-04, PC-05, PC-06 and PC-08. Reviewed shared-contract baseline SHA-256: `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510`; [specification review](../../specifications/SPEC-001_processing_consent_review.md) resolves PCR-01/PCR-02. Later navigation-index changes do not amend those contracts; freeze the final package digests after grooming.

## Increment and context

A Telegram user can give the current explicit consent while finishing initial onboarding, inspect Details in Telegram, see status, withdraw, and explicitly reaccept. Identity durably owns the grant and a caller-UoW fence that downstream AI workers can consume. This task establishes that capability and its real user controls; it does not claim that all existing AI paths are guarded until the dependent task is verified. Do not deploy this intermediate state.

## Scope

- Add the minimal Identity current-state record, published immutable grant tuple and owner-checked read/grant/revoke/final-authorization operations from C1. Reuse the existing `IdentityStore`/shared-UoW approach; no duplicated Billing flag.
- Add one forward migration and register the model for normal schema creation and Identity-owned deletion. Existing profiles, trial/payment records and old confirmations grant nobody.
- Implement one final initial-onboarding consent action with the specified RU/EN notice, Details, Cancel, and policy/draft/owner/revision binding. It atomically grants consent, saves the current draft, activates/restores access via Billing and completes onboarding. Plain yes/historical Confirm is insufficient. Preferences-only editing retains its meaning.
- Extend the existing dirty-tree `/privacy` route/presentation to display current status, explicitly grant/regrant for existing profiles, withdraw, and return from Details to the unchanged current onboarding preview. Acceptance does not replay a prior request or accept payment terms. Keep OpenRouter and provider/local-transcription/retention limitations in the detailed notice.
- Supply the published final-authorization fence and restored-grant invalidation capability consumed by the dependent task. Both use the caller's transaction; the latter changes restored grants to required without creating a retained ledger.

## Out of scope

Provider/voice/Capture/reply enforcement and restore-release wiring belong to [the dependent guarded-processing task](https://github.com/dystrophyless/pin/issues/30). No landing, provider calls, Stars, service startup, live data, payment-policy change, invented retention period, KGD request, deployment, or Git operations. Applicable financial/legal release gates remain open.

## Implementation anchors and invariants

Use the inspected anchors in the spec: Identity `application/contracts.py`, `application/onboarding.py`, `infrastructure/models.py`, `infrastructure/store.py`, `infrastructure/deletion.py`, `api.py`; Telegram `onboarding.py`, `application_worker.py` (`_handle_callback`/`process_application_job`), and `presentation/onboarding.py`, `templates.py`, `callbacks.py`, `tokens.py`; current Alembic metadata registration/history. Preserve the existing uncommitted privacy route/tests instead of replacing them.

The concrete common handoff is [the Guards lock-order and outer-entry map](https://github.com/dystrophyless/pin/issues/30#common-transaction-order-and-entry-ownership), including its entry ownership and ledger/recovery exceptions. Identity implements `F(owner)` through existing `src/pin/persistence/receipts.py:lock_action(session, owner_id, "identity.processing_consent")`, even when no consent row exists, for the full caller transaction lifetime; no history/receipt row is added. Participating transactions use F → live Job/ConversationLease if claimed → Billing lifecycle if used → their owned effect rows. No caller may newly acquire F after prelocking those rows or commit the guard in another session.

This task owns the concrete `process_application_job` outer-entry change: acquire F then explicitly `lock_job_for_effects` before its currently first lifecycle lock, then retain that prefix through the receipt/callback/draft/profile/consent/trial UoW. Shared deterministic branches acquire synchronization only, never a grant prerequisite. Direct Identity grant/revoke/authorization begin F; combined direct onboarding composition obtains any Billing lifecycle lock before token/draft/profile writes and uses the same transaction. Update existing direct native-test callers to the composed entry; a lower Identity helper must not add a late fence after the caller's prelocks. Guards subsequently owns Capture/speech/provider/delivery, direct deletion/purge/maintenance/replay/resolver and recovery outer entries listed in the map. The Identity-owned purge/invalidation capability reuses the same held F and never substitutes for those outer changes. No caller imports Identity ORM tables, and no I/O/inference runs under F. A separate-session read cannot satisfy PCR-01.

Callback token consumption and consent/draft updates commit together or roll back. Unique grant IDs prevent old grants being reused after deletion/return. Existing callback expiry remains unchanged; the policy has no invented expiry. Consent is not purchase/service-terms acceptance or payment authorization.

## Dependencies and ownership

- Depends on: reviewed shared specification and this task/package passing grooming; no unpublished product prerequisite.
- Blocks: [processing-consent-guards.md](https://github.com/dystrophyless/pin/issues/30) and [processing-consent-gate.md](https://github.com/dystrophyless/pin/issues/31).
- Owner: assigned Luna Executor; one product writer. Own the Identity capability/migration, Telegram onboarding/privacy controls and their focused tests. The guarded-processing task may subsequently extend shared adapters/migrations sequentially.
- Priority: high within this consent increment; no priority change to unrelated issues.
- Blocking decisions: none for this bounded local implementation after package readiness. Native execution requires the Orchestrator's disposable-fixture slot and current authorization. Financial/statutory retention remains a separate release blocker.
- You are not alone in the codebase. Preserve other edits and untracked operator documents/output; do not overwrite the parent's work or run a service. No implementation Reviewer is required by the current owner override; the Orchestrator checks raw evidence.

## Acceptance criteria

- [ ] AC-01: Fresh/current migrations create durable required/granted/revoked state and unique grant provenance without backfilling consent; missing/storage-error/stale-policy cases deny. Acceptance/revoke/regrant survive sessions and restarts with expected revisions and new grant IDs.
- [ ] AC-02: Actual RU/EN onboarding and callback routes implement the single explicit action and current-draft/policy binding. Cancel/plain yes/foreign actor/old token/draft/policy/replay cannot grant or duplicate a trial. A forced late UoW failure leaves profile, trial, consent and token consumption rolled back together.
- [ ] AC-03: Details and `/privacy` work before a profile and during onboarding; viewing never grants or cancels/advances the draft. Existing-profile grant/withdraw/regrant preserves preferences/access/allowance and presents truthful status/resend/in-flight wording.
- [ ] AC-04: Two-session native cases prove missing-row grant/revoke races and final authorization serialized with withdrawal through caller commit. The same published fence and the linked concrete lock map support dependent effect transactions without a default-allow or cross-context ORM path. The application/direct-command outer prefix is installed before existing prelocks; direct missing-row cases and concurrent deletion cannot reverse it.
- [ ] AC-05: Identity deletion registration/purge removes the new operational record; retained trial/paid facts are not expanded to consent history. The published restored invalidation is idempotent and scoped to the fenced recovery operation, not ordinary deployment/live-user reset.
- [ ] AC-06: Existing settings, privacy/help/support, Billing purchase terms/receipts/cancellation and onboarding trial behavior retain their approved distinctions; RU/EN plain/Markdown output and callback lengths remain valid.

## Verification plan

Extend `tests/test_identity_onboarding_postgres.py` (`PIN_TEST_ONBOARDING_DATABASE_URL`), `tests/test_privacy_presentation.py`, and the relevant cases in `tests/test_payment_support_routes_postgres.py`; preserve existing tests' independent assertions. Test the published fence using two actual native sessions with barriers after authorization and before commit, not mocks. Execute the actual ingress/application/callback transaction for atomicity/replay failures. Add an isolated migration upgrade assertion against an existing-profile fixture with no consent grants.

Available command shape: `.\.venv\Scripts\python.exe -m pytest -q tests/test_identity_onboarding_postgres.py tests/test_privacy_presentation.py tests/test_payment_support_routes_postgres.py --junitxml=<private-packet>/identity.xml`, after the Orchestrator assigns the modules' actual disposable test URLs. Placeholders are not runnable fixture identities. No skips count as native proof. Run Ruff on changed Python paths, `.\.venv\Scripts\python.exe -m mypy src`, `.\.venv\Scripts\python.exe -m pin.architecture`, and `git diff --check`. Record exact commands, final source/dirty-boundary hashes, JUnit counts, migration output, and exact fixture cleanup. Do not expose database passwords in the evidence or set production URLs.

## Delivery conditions and completion record

Local verification and the Orchestrator's recorded delegated acceptance establish only this capability's completion; the dependent AI-guard task and integration gate are required before representing consent enforcement as complete. No intermediate rollout, commit, publication, merge, or deployment is authorized by this draft. Preserve the original bot release/financial/legal gates. Append implementation/evidence/acceptance records to the real issue after authorized publication; no implementation or runtime proof exists in this draft.


## Grooming correction record

PCR-P01 MEDIUM is addressed by this explicit Identity/application entry ownership and the common [Guards map](https://github.com/dystrophyless/pin/issues/30#common-transaction-order-and-entry-ownership). The independent package recheck is READY; no implementation/native proof is asserted.
