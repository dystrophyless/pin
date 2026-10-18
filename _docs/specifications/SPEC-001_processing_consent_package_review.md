# Processing-consent task grooming and package review

Date: 2026-10-19
Round: 2, bounded correction recheck; reviewer writes only this report. Planner corrections are confined to the three task drafts.
Verdict: **Ready within the reviewed task/package scope.** PCR-P01 is resolved in the corrected drafts; the bounded recheck found no remaining finding or introduced contradiction. Execution order is Identity → Guards → Gate, with source/fixture and native-evidence prerequisites below. This is task/specification readiness, not implementation review, runtime proof, publication authorization or acceptance.

## Frozen package and evidence limits

| Artifact | SHA-256 |
|---|---|
| `_docs/specifications/SPEC-001_processing_consent.md` | `752775ab8593b2569ee630a2273197363df897125b42a3384f4eb30862933b4a` |
| `_docs/task-drafts/SPEC-001/processing-consent-identity.md` | `b4514e1aadb42fe84a333e028a75c1c976732332acf5da52a713c682d909b7b0` |
| `_docs/task-drafts/SPEC-001/processing-consent-guards.md` | `8242fd1caba2351caa26664d2cdb8534c3d6aa608ae9bd3900eef6211de66c05` |
| `_docs/task-drafts/SPEC-001/processing-consent-gate.md` | `3a513890c8e6cd78498636ef570aa7859bee775f2967c4e1ed57bb05a6a338fe` |

The tasks are unpublished local drafts, not live issues. Their references to shared-contract digest `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510` remain traceable to the completed specification review; the current index/navigation changes do not change C1–C5 or PC-01–PC-08. Publication must use the final frozen package and reconcile existing privacy/recovery/release issue dependencies.

Source inspection uses the existing `repository root` checkout at previously verified HEAD `abee1c575a3677e024fa24eaa49cfcdb33b62af0`, with prior uncommitted privacy work preserved. Followed local Ponytail, spec-task-review, task-package-review-loop and decomposition/workflow guidance. Used semantic search before reading the newly relevant lock helper and fixture/deletion seams. Executed only document/source reads, file-existence checks, Git status and SHA-256 checks. No tests, database/service/provider operations, actual Telegram/Stars, GitHub changes, commits, deployment or restore were performed. No implementation correctness is established. The exhausted provider allowance and mandatory unresolved applicable financial/statutory-retention/legal release gates are unchanged.

## Bounded correction recheck

Rechecked on 2026-10-19 against the current frozen hashes above. Scope was PCR-P01's mapped lock order, outer-entry ownership, sibling consistency and introduced contradictions; no implementation review or runtime operation was performed.

PCR-P01 is **resolved as a documentation/grooming finding**. Guards lines 41–64 now own the common map: stable `identity.processing_consent` transaction key; F → live Job/ConversationLease when claimed → lifecycle when used → owned target rows; one owner's F per transaction; no lower helper newly acquiring F after prelocks. The map assigns the application/direct Identity prefix to Identity and the Capture/speech/provider/result/allowance/delivery/deletion/maintenance/replay/resolver/recovery entries to Guards. It moves prelocked maintenance batches to revalidated per-owner transactions, moves replay ledger work behind the common prefix, and handles first-mapping conflict by rollback/fresh-UoW retry rather than a second-owner fence. Ordinary delivery takes synchronization only. Numeric late usage receipts and pure scheduling/export work cannot later wait for F. Global recovery-row transactions are separated from per-owner invalidation and verify exact-generation completion before release; audio cleanup stays outside the consent transaction.

Identity lines 39–41 reference that same map and own the concrete application/direct entry changes. Guards AC-03 and its verification plan require the mapped concurrency/liveness evidence, with bounded timeouts and final-state checks. Gate lines 38/52 consume the same order and proof, including ordinary delivery, deletion/maintenance/replay, mapping-conflict retry and recovery generation. These edits resolve the deferred contract without changing the shared specification or adding consent as a prerequisite to deterministic actions.

Both the correction and sibling agreement are **confirmed** by source/document inspection. Actual liveness, grant/effect ordering, restore and delivery behavior remain **unconfirmable** until implementation and native checks. No further product decision or implementation-review stage is required by this verdict. The drafts' correction records correctly report their own pre-recheck state; this report records the completed independent recheck.

Initial round-1 task digests, preserved for correspondence: Identity `f9aa5448dded7ec3d1285cc0bc027edebe614200f8556f221642320e4ca0a778`; Guards `d8f07a582afc1cdf499d8a3f723da055e238ec5b4e9d78757fb73acfbc472967`; Gate `5d8216dadf3881dddf1f555206f6c448714ac6eef70ba6b4e18471b1c90fe05a`. The shared specification digest did not change.

## Individual task verdicts

| Draft | Grooming verdict | Coverage and boundary |
|---|---|---|
| Identity/onboarding | Ready; PCR-P01 resolved | Durable grant/current policy, missing-row fence, one-action RU/EN onboarding, Details/status/withdraw/regrant, rollback, initial migration/purge registration and published recovery capability are covered. It explicitly disclaims complete AI enforcement and prohibits intermediate rollout. Its concrete lock-order handoff and owned outer entry are now specified. |
| Guards | Ready; PCR-P01 resolved | Admission, voice, every provider attempt, replay/results/proofs, AI-derived confirmations/replies, deletion/TR-03, restore and compatible rollback are one coupled authorization boundary. It owns the necessary outer-call-site changes after Identity releases shared files. Its concrete common map assigns all participating outer entries and preserves deterministic authorization. |
| Gate | Ready as the dependent gate; waits for both verified predecessors | Covers PC-01–PC-08 through native/offline composition, source compatibility, race-order proof, local actual restore and deterministic regressions. It cannot quietly fix product code or waive missing native proof. |

## PCR-P01 — MEDIUM — Complete the lock-order handoff during grooming (resolved)

**Original evidence status:** confirmed round-1 task/shared-contract readiness gap, now resolved by the corrected map and sibling handoff above. The historical proof/action below describes the initial drafts. Runtime deadlock or correctness remains unconfirmable before implementation.

**Class:** architectural execution seam / cross-task grooming prerequisite.

**Affected tasks/AC:** Identity line 39, AC-02/AC-04/AC-05; Guards line 41, AC-03/AC-06/AC-07; Gate consumes the resulting contract through AC-02/AC-04. Shared specification C1 line 42 explicitly requires task grooming to establish one consistent lock order including lifecycle/job locks.

**Proof:** Identity line 39 establishes consent first but then says to document the concrete subsequent ordering and dependent call-site adjustment. Guards line 41 says to apply a consistent documented order for existing locks and record the map “before implementation.” Neither draft provides that map. This leaves a reviewed grooming prerequisite for the future Executor rather than a stable predecessor/consumer contract.

The actual entry points show why the missing map is material:

- `src/pin/adapters/telegram/application_worker.py:143` takes the lifecycle lock before `record_job_effect_once` at lines 147/207; `src/pin/platform/jobs.py:378–390` establishes that this receipt path takes the job/claim lock.
- `src/pin/contexts/notifications/infrastructure/delivery_worker.py:758–780` starts with the job/claim, then outbox, then lifecycle locking.
- `src/pin/contexts/conversation/infrastructure/usage.py:174–177` enters the provider recorder UoW and its job fence before provider-row work.
- `src/pin/adapters/telegram/deletion.py:43–55` enters lifecycle/context purge before the Identity purge, while `src/pin/adapters/retention/deletion.py:101–114` enters tombstone/lifecycle work before calling that purge.

Adding a fence inside only the Identity adapter would be too late for callers that already hold these locks. The drafts recognize the need to move prelocks, but do not yet establish the agreed order and the outer entries each sequential task must change. This is not evidence that the new feature already contains a deadlock, and it does not justify a general locking refactor.

**Scoped package action:** Put a concise concrete order/call-site map in the owning individual draft, with the sibling referencing it as the common contract. State the stable Identity advisory key and lock lifetime; order consent relative to job/conversation-lease, lifecycle, token/draft/effect and recovery/purge locks where those coexist; name the outer transaction entries that must acquire consent before existing prelocks and assign them to Identity or Guards. Include absence/direct-call cases and delete/export/replay/recovery entry points. Explain which existing nonparticipating paths are preserved and why they cannot acquire the same locks in reverse. Keep this bounded to participating transactions; do not require a consent grant for deterministic actions. Any physical choice must preserve current claim/lifecycle/idempotency semantics and I/O exclusion.

The actual `src/pin/persistence/receipts.py:15–21` helper is a suitable available mechanism: it uses a transaction advisory lock on owner/action and does not depend on a consent row or insert a new history record. No new global lock service is needed. The missing work is the concrete invocation/order contract, not dependency research or a new product decision.

**Expected next evidence:** Recheck the corrected draft map against the named outer callers and its sibling handoff; freeze new digests. During implementation, the already specified two-session native tests must prove both commit orders and liveness at the actual call sites, including deletion/replay where relevant. No runtime proof is required merely to resolve this documentation finding, and no additional implementation-review stage is introduced.

## Package coverage and execution handoff

| Shared criterion | Implementation owner | Final proof owner |
|---|---|---|
| PC-01: one explicit action, binding, atomic onboarding | Identity | Gate AC-01 |
| PC-02: old-user denial, no processing/spend, details/resend | Identity owns controls; Guards owns admission and denied-work behavior | Gate AC-01 |
| PC-03: first/later dispatch, accurate receipts, stale-result suppression | Guards, consuming Identity fence | Gate AC-02 |
| PC-04: missing-row/admission/concurrency/replay/native composition | Identity owns fence; Guards owns every consumer and lock-order integration | Gate AC-02 and source-bound matrix |
| PC-05: deterministic and Billing behavior preserved | Both implementation tasks at their affected seams | Gate AC-03 |
| PC-06: deletion/TR-03, migration and old-generation denial | Identity owns model/purge capability; Guards owns lifecycle composition/provenance | Gate AC-04 |
| PC-07: restored-grant invalidation and fail-closed release | Identity owns invalidation capability; Guards owns recovery wiring | Gate AC-04 |
| PC-08: RU/EN notice/details/withdrawal and truthful limits | Identity; Guards preserves denial/in-flight meaning | Gate AC-01/AC-05 |

With PCR-P01 resolved, the stable order is **Identity → Guards → Gate**. Identity owns its capability/migration/onboarding/privacy and focused tests. Guards is the sole subsequent product writer for enforcement/lifecycle and may extend shared adapters/migrations/tests only after release by Identity. Gate then owns only integration tests/fixtures/evidence; product defects return to the relevant implementation task. Parallel editing of these shared surfaces is unsuitable. The Orchestrator checks raw evidence and records the delegated decision; the current owner override requires no independent implementation Reviewer.

The Guards task is broad but its work is one coupled security contract: accepting provider checks while voice/replay/reply/recovery bypasses remain would give a false completion claim. Identity is a legitimate verified foundation/user-control increment with explicit intermediate limits. Gate can reuse compatible source-bound predecessor proof and add only missing chain scenarios. No independent feature/refactor is hidden in these boundaries.

## Verification and readiness limits

**Confirmed:** All PC criteria have explicit owners and final proof. Initial final-action binding and rollback, immutable admission, every-attempt authorization, zero-total versus zero-additional calls, provider/user usage separation, legacy proof rejection, deterministic exceptions, tombstone return generations, recovery idempotence and fenced rollback are covered. Existing helper and listed test/runbook files exist. No new quality/capacity/economics benchmark is imposed.

**Refuted concerns:** The drafts do not claim that Identity alone finishes enforcement, that the gate can implement hidden product fixes, that stale work becomes valid on regrant, or that processing consent authorizes purchases or removes legal release gates.

**Partial:** Verification plans identify concrete modules, fixture variables, barriers and expected assertions, but placeholder commands/URLs are not executable fixture assignments. This is explicitly acknowledged and is an execution prerequisite, not an unresolved product decision. Actual existing restore fixtures impose strict local host/user/version/directory/name guards (`tests/test_retention_restore_postgres.py:65–80,112–134`); the Orchestrator's fixture manifest must satisfy those checks or a separately scoped test-fixture adaptation must preserve equivalent ownership guards. Never weaken them to obtain a PASS.

**Unconfirmable:** Native atomicity/liveness, fixture compatibility, full RU/EN behavior, deletion/restore correctness and final-source integration cannot be established from these drafts. The gate properly requires zero-skip native evidence and an actual disposable restore, distinguishing injected shell smoke from data/recovery proof. Missing permission/fixture availability blocks the corresponding run; it does not justify a production/service operation or a silent PASS.

The reviewed current digests are ready as a coherent package; PCR-P01 and the affected sibling contracts are rechecked. Execution still waits for the authorized dispatch and exact sole native fixture slot/module-specific disposable source-target assignments. Publication remains separate and must reconcile live issue dependencies and preserve reviewed bodies before replacing published drafts with stubs. Mandatory financial/statutory-retention/legal launch requirements remain open even if local consent proof later passes.

Only this review report was written. No runtime verification or external operation occurred; all other edits were preserved.
