# SPEC-001 processing consent: independent specification review

Date: 2026-10-19
Verdict: **Ready for task decomposition within the reviewed scope.** PCR-01 and PCR-02 are resolved as specification findings in the corrected revision. The bounded recheck found no remaining finding or introduced contradiction. This is specification readiness, not implementation verification, legal approval, deployment authorization, or product acceptance.

## Reviewed boundary and limitations

- Current rechecked specification: `_docs/specifications/SPEC-001_processing_consent.md`, SHA-256 `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510`.
- Initial reviewed revision: SHA-256 `54d466ddd618d90acd60d743e0048813157c83963f3b318171f87be308d65a0d`. The detailed findings below preserve the initial evidence; their requested contract corrections are now closed.
- Actual checkout: `repository root`, HEAD `abee1c575a3677e024fa24eaa49cfcdb33b62af0`.
- Preserved dirty source: `src/pin/adapters/telegram/application_worker.py`, `src/pin/adapters/telegram/presentation/templates.py`, `tests/test_payment_support_routes_postgres.py`, and untracked `tests/test_privacy_presentation.py`. Other pre-existing untracked documentation/output was left untouched.
- Reviewed AGENTS, local Ponytail and spec-task-review instructions, HANDOFF, orchestration owner override, SDD/decomposition guidance, current MVP amendments/TR-03 and the relevant architecture contracts. Used semantic code search before direct source inspection.
- Owner choices were supplied in the review assignment: one explicit initial-onboarding action, short provider-neutral notice, external text/transcript/relevant-plan processing, OpenRouter in details, no checkbox/payment authorization, consent before existing users' next AI interaction, and withdrawal. The assignment is the provenance for these choices; an external conversation or legal opinion was not independently obtained.
- Executed checks: source/document inspection, Git status/HEAD, and specification digest. No tests, migrations, database operations, services, provider calls, actual Stars, deployment, restore, KGD requests, or GitHub mutations were performed. Runtime guarantees below remain unproved until implementation and checks exist. The review does not claim legal sufficiency or resolve mandatory financial-record retention.

## Bounded correction recheck

Rechecked on 2026-10-19 against the current digest above. Scope was PCR-01/PCR-02 corrections and connected requirements/AC for introduced contradictions; no new implementation audit or scope expansion was performed.

| Finding | Current evidence | Disposition |
|---|---|---|
| PCR-01 HIGH | C1 line 42 explicitly requires the published same-UoW final fence to serialize through the protected commit against grant/revoke/deletion, including absent-row creation and consistent lifecycle/job lock order. C3 lines 67 and 71 apply it to provider start, AI results/proposals/proofs, Save/Confirm and reply attempt markers; C4 line 79 orders committed withdrawal and acknowledgement. PC-04 line 102 now requires the corresponding native inter-session read-to-commit barriers. The I/O exclusion and already-started-attempt limits remain intact. | Resolved in the specification; implementation/native proof pending. |
| PCR-02 MEDIUM | C3 line 67 distinguishes zero total calls before the first dispatch from zero additional dispatches before later repair/retry, preserves started/unknown receipts, and keeps existing Billing outcomes separate from provider cost. PC-03 line 101 requires counters zero/one in separate cases and independent receipt/reservation assertions. | Resolved in the specification; runtime proof pending. |

Evidence classification: both corrections are **confirmed** by the current text. Actual fence correctness, call counts, receipts and user Billing effects are **unconfirmable** before final-source execution. Existing partial presentation evidence and legal/financial release limits below are unchanged. The planner's review-correspondence section correctly describes its own pre-recheck status; this review record supplies the completed independent recheck and exact reviewed digest.

## Original actionable findings (resolved)

### PCR-01 — HIGH — Serialize final consent authorization with each protected effect

**Original evidence status:** confirmed specification gap in the initial revision; resolved by the current correction. Runtime behavior remains unconfirmable before implementation.

**Class:** shared-contract / architectural concurrency seam.

**Affected contracts:** C1, C3, C4; PC-03 and PC-04, with PC-06/PC-07 reusing the same fence.

**Evidence:** Specification line 67 explicitly makes authorization and the provider started-call marker atomic. Lines 71–73 only say to verify the grant at result application, AI-derived callbacks, and reply delivery. Line 42 serializes consent mutations, but does not explicitly serialize those readers/effects against withdrawal. Lines 79–81 define committed withdrawal as effective and promise suppression of uncommitted results and pending proofs/replies.

A plain current-grant read followed by a write allows this schedule: a worker reads `granted`; withdrawal commits and is acknowledged; the worker then commits an AI-derived result, applies Save, or creates a new reply dispatch marker. Such an implementation can satisfy a literal instruction to “verify” yet violate the stated effective point. A queue ordered by owner does not establish this guarantee across speech/application/delivery workers or direct assistant entry points.

The actual seams already provide short effect transactions: `src/pin/adapters/telegram/capture.py:334–399` applies a model result and queues its reply; `capture.py:560–625` applies callback effects; `src/pin/contexts/notifications/infrastructure/delivery_worker.py:904–927` creates a durable reply attempt before Telegram I/O at line 936. For the provider, `src/pin/contexts/conversation/infrastructure/usage.py:169–204` owns its own short UoW in `start_call`. An independent authorizer read in a different session cannot make that existing marker atomic with withdrawal. `_docs/ARCHITECTURE.md:260–266` already permits shared-UoW orchestration through published contracts and requires consistent lock ordering.

**Scoped action:** State that Identity's published authorization fence must participate in the caller's short UoW and serialize with grant/revoke/deletion, remaining effective through the protected commit. Apply it to provider start markers, AI-result/proposal/proof application, AI-derived Save/Confirm, and AI-reply attempt authorization. Choose a consistent lock order during task grooming; consumers must not query Identity ORM tables. Keep all I/O and inference outside the transaction. Define the order by committed effect/dispatch marker versus committed withdrawal, preserving the documented exception for attempts already authorized/started. No new global lock service or retained consent ledger is required.

**Expected proof:** Native PostgreSQL barriers pause immediately after the final authorization read and before effect/marker commit while withdrawal runs in another session. Either the effect/marker commits before withdrawal can acknowledge, or withdrawal wins and the effect/marker is denied. Exercise result application, Save/Confirm and reply authorization in addition to provider dispatch. Regrant must not authorize the old tuple. Record the final source, disposable environment, and zero-skip results.

### PCR-02 — MEDIUM — Qualify the zero-call promise for a denial before a repair attempt

**Original evidence status:** confirmed wording contradiction in the initial revision; resolved by the current correction. No implementation bug claimed.

**Class:** shared-contract / acceptance precision.

**Affected contracts:** C3 line 67; PC-03.

**Evidence:** Line 67 covers every actual attempt, including repairs/retries, and then says, “Denial before dispatch causes zero provider calls and no AI-turn consumption.” Line 69 and PC-03 explicitly allow withdrawal after a first dispatch and require blocking a later repair. That case already has a provider call, so an unqualified zero-call assertion cannot hold. The same paragraph separately requires preserving started/unknown usage facts. Actual baseline behavior keeps provider receipts separate from user holds: `src/pin/contexts/conversation/application/service.py:415–443` stores/settles successful results, while lines 471–488 refund failed-turn holds without erasing provider cost facts.

**Scoped action:** Limit zero total calls to denial before the first dispatch of that work item. For denial after an earlier attempt started, require zero additional dispatches, suppression of obsolete output, preservation of accurate started/unknown provider receipts, and the existing applicable Billing settlement/refund behavior. Do not invent a new financial policy or equate provider cost with a user AI-turn charge.

**Expected proof:** Separate fake-transport cases for denial before call one (counter zero), and withdrawal after call one but before repair (counter remains one). Assert usage evidence and user reservation/settlement independently according to the existing failure/success path.

## Other reviewed claims and boundaries

| ID | Evidence status | Assessment |
|---|---|---|
| R-01 | confirmed | Identity ownership and a published snapshot/port fit the repository. The inspected baseline has preferences/onboarding contracts, not persisted processing consent; the draft describes new work rather than pretending it exists. |
| R-02 | confirmed | C2 binds initial acceptance to owner, draft generation/revision, consent revision and displayed policy. Existing callback support supplies owner/action/target/version/expiry; `application_worker.py:861–910` currently translates the old Confirm into plain confirmation. A new explicit action and Identity atomic command must replace that initial path as specified, while preferences-only confirmation keeps its meaning. Encoding/validating the extra claims remains implementation work, not an existing capability. |
| R-03 | confirmed | Admission can use trusted owner mapping before a profile exists. `ingress.py:229–255` resolves the actor before durable inbox/job insertion. The stamp must be server-owned, persisted with the admitted item, and immutable on duplicate receipt; later acceptance must not restamp old input. PC-04 is the appropriate native proof. Deterministic onboarding/privacy/payment controls must remain outside the AI prerequisite. |
| R-04 | confirmed | Grant provenance across Capture, mutation previews/proofs, cached results and replies is necessary to reject stale AI-derived actions. Capture handles both AI-derived and deterministic callbacks, so blanket gating its callback worker would violate C3/PC-05. Existing saved plans, independent deterministic previews and reminders retain their current authorization. |
| R-05 | confirmed | Deletion/TR-03 treatment follows the approved minimum return facts. `identity/infrastructure/deletion.py:12–13` delegates schema-owned purge; model registration must include the new record. Unique grant IDs address reused canonical owner IDs without extending the retained return record to consent evidence. Generation-aware tombstone behavior remains a required check. |
| R-06 | confirmed | Invalidating all restored grants is a proportionate conservative rule: a snapshot can predate withdrawal, and no separately approved complete consent ledger exists. Existing recovery already fences the runtime and releases on reconciled evidence (`platform/recovery.py:153–184` and `_release`). Use an Identity-owned invalidation capability behind that fence; do not add a new retained ledger, global live-user reset, or ordinary-deployment reacceptance. Idempotent recovery completion is still to be implemented/proved; `_arm` currently rejects an already-fenced gate. |
| R-07 | confirmed | Rollback fencing and release compatibility are within the smallest complete consent increment because an older provider/speech worker could otherwise bypass it. This review authorizes no rollout. |
| R-08 | confirmed | Processing consent remains distinct from service/purchase terms, payment authorization, subscription cancellation and refunds. The MVP purchase rule at `SPEC-001_pin_mvp.md:495` remains applicable. This review does not add service-terms acceptance to the consent action. |
| R-09 | partial | The detailed-notice reach and preservation of the dirty-tree `/privacy` route are explicit. Final RU/EN rendering, callback escape/length behavior, Details/return-to-preview flow and truthful OpenRouter/provider/local-transcription copy have not been implemented or visually checked for this increment. PC-01/PC-02/PC-08 cover this evidence. |

### Evidence buckets

- **Confirmed:** PCR-01 and PCR-02 were initial document-contract findings and are now resolved by confirmed text corrections; R-01 through R-08 have supporting source/requirement evidence at the stated scope.
- **Refuted concerns:** No evidence supports treating current profiles/payments as consent, requiring a new checkbox, making every deterministic action depend on consent, recalling in-flight external calls, or restoring consent through TR-03. The draft explicitly excludes these interpretations (R-02/R-04/R-05/R-08).
- **Partial:** R-09 establishes the specified presentation scope, not implemented RU/EN behavior.
- **Unconfirmable:** The future atomic fences and integration behavior in PCR-01/PCR-02/R-02–R-07 require final-source tests; deployed/runtime evidence and legal sufficiency are outside this inspection. Missing runtime evidence is not labeled a current runtime defect.

## Next bounded step

Use rechecked specification digest `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510` to decompose the already identified Identity/onboarding, guarded processing/revocation/recovery, and integration outcomes into individual task drafts. Carry the now-explicit PC-03/PC-04 checks into their existing AC without adding a model-quality benchmark or implementation-review ceremony. Preserve the mandatory unresolved applicable Kazakhstan financial/statutory-retention and legal release gates; they do not require guessing a period and do not block independent consent implementation. Runtime/native verification remains required against final implementation source.

Nothing was implemented or tested. All pre-existing edits were preserved; provider allowance remains exhausted, and no external operation was attempted.
