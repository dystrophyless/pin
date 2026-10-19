# Prove the integrated Telegram processing-consent contract

**Owner scope amendment, 2026-10-20:** MVP v0.21 waives jurisdiction-specific financial-retention/entity/tax validation as an acceptance/closure gate (WAIVED / NOT ASSESSED). Preserve all technical AC, native/integrated and delivery requirements, Terms of Use/Privacy Policy, consent, support/payment transparency, operational retention/deletion/recovery and minimized payment evidence. No numeric TTL or financial deletion is authorized. This local contract amendment has not been synchronized to the live issue.

<!-- pin-task: SPEC-001/processing-consent-gate -->

Publication: synchronized with issue #31 (https://github.com/dystrophyless/pin/issues/31) on 2026-10-19 after package READY.

## Task kind

Verification gate.

## Workflow status

Locally verified by the Orchestrator; source remains uncommitted/undeployed and remote delivery reconciliation remains open. See [local integrated acceptance](../../evidence/processing-consent-integrated-20261020.md).

## Requirement and specification

[SPEC-001 processing consent](../../specifications/SPEC-001_processing_consent.md), all C1–C5 and PC-01–PC-08; reviewed shared-contract SHA-256 `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510`; [specification review](../../specifications/SPEC-001_processing_consent_review.md). PCR-01/PCR-02 native concurrency and accounting proofs remain mandatory; their document resolution was not runtime proof.

## Increment and context

Produce one source-bound integrated verdict showing that the explicit consent UX actually controls the deployed-code-equivalent processing composition, survives replay/deletion/recovery and leaves deterministic/Billing functions intact. This gate proves the local increment; it does not replace missing implementation, technical delivery or final acceptance. Financial legal validation is waived under v0.21, not resolved as compliant.

## Scope

- Exercise the full actual ingress/application/onboarding/callback/speech/Capture/provider-attempt/reply composition with native persistence and offline transports.
- Cover current-policy initial consent, existing users, denial/resend, in-Telegram Details, withdrawal/regrant, stale artifacts, deletion/return and restored grants in RU and EN.
- Reuse predecessor proofs when their final source/fixtures remain compatible; add bounded integrated scenarios where isolated proofs do not establish the chain. Test-only integration additions and scoped evidence are allowed; route product defects to the owning implementation task.
- Map every PC criterion to final-source native/unit evidence, limitations and an explicit verdict; preserve historical failures and separate local evidence from live/deployed acceptance.

## Out of scope

No new feature, broad refactor, hidden implementation fix, fresh implementation Reviewer, quantitative model-quality/capacity/economics benchmark, external provider call, real Stars, actual Telegram send, KGD inquiry, landing check, live mutation, service startup, hosted restore/deployment, GitHub changes or Git action. Do not turn an unverified legal policy into a passing gate.

## Anchors and invariants

The predecessor drafts enumerate implementation paths and focused tests. Build on `tests/test_core_lifecycle_integration_postgres.py`, `tests/test_identity_onboarding_postgres.py`, `tests/test_processing_consent_postgres.py` (introduced by the guard task), `tests/test_speech_worker_postgres.py`, `tests/test_notification_delivery_postgres.py`, and `tests/test_retention_restore_postgres.py`. Add a small `tests/test_processing_consent_integration_postgres.py` only if existing seams cannot express the package chain cleanly; use the test-only consent fixture URL/ownership established by the guard task. No new product configuration is implied.

Use the final changed source, including preserved prior privacy work. Synthetic accepted-grant fixtures are explicit; no default fixture acceptance may hide the old-user denial case. A provider-call counter of zero and a user-turn counter of zero are separate assertions. Consume the reviewed [concrete transaction lock-order/outer-entry map](https://github.com/dystrophyless/pin/issues/30#common-transaction-order-and-entry-ownership), not an Executor-chosen order. A barrier after the final authorization read must still prove serialized commit, not only a check before the race window. Already-started external-attempt limits remain honest. Existing planning/access/retention invariants remain intact.

## Dependencies and ownership

- Depends on: [processing-consent-identity.md](https://github.com/dystrophyless/pin/issues/29) and [processing-consent-guards.md](https://github.com/dystrophyless/pin/issues/30), with verified final-source outputs and reviewed package.
- Blocks: representing this consent increment as integrated/accepted; evidence is subsequently reconciled into the existing bot privacy/recovery/release issue gates by the Orchestrator/authorized Publisher. The financial legal-validation gate is waived under v0.21; existing technical/product-policy dependencies remain.
- Owner: assigned Luna Executor for test/evidence production; Orchestrator checks raw artifacts and makes the delegated acceptance decision. No separate implementation Reviewer under the owner override.
- Write ownership: only consent integration tests/evidence and necessary test fixtures after predecessors release their files; no product changes without a separate scoped fix dispatch. You are not alone; preserve others' work.
- Priority: high within this increment.
- Blocking decisions: no new product decision. Missing predecessor proof or disposable native slot blocks dependent runs; report exact resume condition rather than run against production or restart a service.

## Acceptance criteria

- [x] AC-01: Fresh RU/EN flows show the correct concise notice/Details/current preview and one explicit initial action; replay/restart saves consent/preferences/trial once. Existing-profile denial makes zero processing calls, acceptance preserves access/preferences and requires a newly admitted request. This maps PC-01/PC-02/PC-08.
- [x] AC-02: Withdrawal/regrant scenarios demonstrate immutable admission provenance, no additional repair after first call, accurate provider receipts/user holds, suppression of obsolete returned/cached/proposed/saved/replied AI artifacts, and honest already-started attempt handling. Native barriers cover provider marker, result/proof application, Save/Confirm and reply marker in both race orders. Include bounded liveness/state checks for the mapped direct/application, ordinary-delivery, deletion/maintenance/replay entries, absence, admission mapping-conflict retry and exact-generation recovery completion; no second-owner fence or target-before-F wait is permitted. This maps PC-03/PC-04.
- [x] AC-03: Without consent, actual persisted deterministic help/privacy/support/settings/agenda/subscription/payment-control/deletion/confirmed-task/reminder paths retain their approved behavior and do not fabricate a grant or payment authorization. Synthetic payment events only; no Stars. This maps PC-05.
- [x] AC-04: Deletion/TR-03 return and actual disposable older-snapshot restore cannot resurrect consent or old-grant work; fresh consent permits only new input, restore retry is idempotent and failure remains fenced. Existing deletion/delivery/payment/usage prerequisites remain enforced. This maps PC-06/PC-07.
- [x] AC-05: All PC-01–PC-08 rows have compatible final-source proof, no unresolved implementation defect and an honest environment/delivery verdict. Missing required native or integrated proof yields blocked/partial, never a blanket PASS. The financial legal-validation gate is waived under v0.21; missing technical/delivery proof and remaining product-policy requirements still block acceptance.

## Verification plan

Before running, freeze the actual dirty-source manifest/commit boundary and inspect predecessor raw evidence. The Orchestrator allocates the sole native slot and exact manifest-owned disposable databases/restore source-target pairs; no fixed or historical catalog is implied. Offline fakes count provider/download/recognizer/Telegram calls. Native cases must execute with zero skips. Use existing fixture variables per module; the new consent integration module uses the guard task's test-only consent database variable and ownership rules.

Run the bounded new/affected consent modules with `.\.venv\Scripts\python.exe -m pytest -q <reviewed-module-list> --junitxml=<private-packet>/consent-integration.xml`; resolve placeholders to the actual final module/fixture manifest before execution. Include whole affected modules needed to verify fixture migration and deterministic regressions, not every unrelated test by default. Run final-source Ruff, mypy, architecture check and `git diff --check` once after final changes. Repeat only for changed/failing seams. Report precise commands/results and source hashes; do not relabel an older full regression as current.

The evidence packet contains a PC-to-proof matrix, raw JUnit/stdout, race-order observations, fake dispatch counters, separate provider/user-usage assertions, migration/restore/fencing evidence, fixture cleanup/catalog comparison and known limits. Keep credentials, private utterances and user identifiers out of public summaries. File location/retained private artifacts are evidence, not a second task-status queue.

## Delivery conditions and completion record

The gate can receive local consent-increment acceptance only after the Orchestrator inspects the final-source raw evidence and records the rule-based decision and remaining limits. Do not imply the human personally reviewed it. Local success does not mean merged/deployed, hosted-tested, legally approved or commercially accepted. Existing technical release issue dependencies remain open. The former mandatory Kazakhstan financial/statutory-validation decision is waived under v0.21, not assessed as passed. Any later delivery action needs its own authorization. No evidence was executed or acceptance granted by this draft.


## Grooming correction record

The PCR-P01 correction reconciles this gate's concurrency/liveness proof with the common Guards map and Identity entry ownership. The independent package recheck is READY. This adds no new product semantics or implementation-review stage.
