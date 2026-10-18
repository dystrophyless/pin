# SPEC-001: Explicit consent for external AI processing

Status: draft for independent specification review
Created: 2026-10-19
Scope owner: Pin owner
Baseline: `abee1c575a3677e024fa24eaa49cfcdb33b62af0` plus the existing uncommitted Telegram `/privacy` presentation and route changes inspected on 2026-10-19.
Sources: owner decisions relayed in this planning assignment; [MVP contract](SPEC-001_pin_mvp.md), [HANDOFF](../HANDOFF.md), [orchestration](../orchestration.md).
Approval: the owner approved the product choices recorded below; this implementation contract has not yet passed specification review. No implementation, deployment, legal approval, or acceptance is claimed.

## Goal, authority, and scope

Enable explicit, durable, revocable consent before Pin sends a user's text, voice transcript, or relevant saved-plan context to external AI providers. Obtain it within onboarding using one explicit final action, with a concise provider-neutral notice and a working details button. Existing users must give the same consent before their next AI request. Reading a notice, sending a message, owning a profile, using a trial, or paying does not grant consent.

This increment covers the Telegram bot, including text, voice, advice, clarification, queued/replayed work, deletion/return, and restored snapshots. Landing work remains deferred by the owner. Payment authorization remains a separate Billing contract. No KGD inquiry, external provider requests, real Stars tests, new spend, retained-data lifetime, or assertion of legal compliance is authorized by this document. The existing 24-attempt provider allowance is exhausted.

## Inspected implementation anchors

Paths are repository-relative. These are current implementation anchors, not claims that consent already exists.

| Boundary | Inspected code and required reach |
|---|---|
| Private ingress and queues | `src/pin/adapters/telegram/ingress.py`: `persist_update`, `private_actor`, `PostgresTelegramOwnerResolver.resolve`; trusted owner mapping precedes durable `receive_update`; voice uses the speech lane; ordinary work is conversation-ordered. |
| Onboarding truth | `src/pin/contexts/identity/application/onboarding.py`: `begin_onboarding`, `answer_onboarding`, `confirm_onboarding`; final initial confirmation saves preferences, activates the trial, and completes the draft in one transaction. Current free-text yes/confirm is not consent. |
| Identity persistence/public API | `src/pin/contexts/identity/application/contracts.py`: `IdentityStore`, `OnboardingDraft`; `infrastructure/models.py`: `PreferencesRecord`, `OnboardingRecord`; `infrastructure/store.py`: `SqlAlchemyIdentityStore`; published boundary `src/pin/contexts/identity/api.py`. There is no persisted processing-consent contract in the inspected baseline. |
| Telegram onboarding and privacy | `src/pin/adapters/telegram/onboarding.py`: `handle_private_text`; `presentation/onboarding.py`: `render_onboarding`, `_step_buttons`; `application_worker.py`: `process_application_job`, `_handle_callback`; `presentation/templates.py`: `plain_privacy`, `render_privacy`. The dirty-tree `/privacy` route and bilingual copy must be preserved and extended. |
| Callback authorization | `src/pin/adapters/telegram/presentation/callbacks.py`: `CallbackTokens.issue/consume`, `CallbackBinding`; `presentation/tokens.py`: `SqlAlchemyCallbackTokenStore`. Existing tokens bind owner, action, target, target version, and expiry. Reuse existing expiry constraints; do not invent a consent expiry. |
| Text, transcript, advice | `src/pin/adapters/telegram/capture.py`: `_process_capture_job`, `continue_transcribed_capture`, `_process_capture_callback_job`, `_queue_reply`; context includes owner-filtered plans before `assistant.interpret`. Result application and replay currently recheck account lifecycle, but lack consent fencing. |
| Provider attempts | `src/pin/contexts/conversation/application/service.py`: `BoundedPlanningAssistant.interpret`, `with_allowance`; each attempt calls usage `start_call` before transport `complete`, including output-repair attempts. `infrastructure/usage.py`: `SqlAlchemyTurnAllowance`, `SqlAlchemyProviderUsageRecorder` own reservation/receipt integration. Consent must not be inferred from these financial/access records. |
| Voice | `src/pin/adapters/telegram/speech_worker.py`: `process_speech_job`, `_continue_capture`; reserves allowance, downloads from Telegram, decodes, transcribes locally, persists a transcript, then continues Capture; existing replay can resume a stored transcript. |
| Reply delivery | `src/pin/contexts/notifications/infrastructure/delivery_worker.py`: `deliver_reply_job` authorizes and records an attempt before Telegram I/O. Consent applies to AI-derived reply artifacts, not every reply/reminder. |
| Deletion and return | `src/pin/adapters/telegram/deletion.py`: `purge_account`; `src/pin/contexts/identity/infrastructure/deletion.py`: `purge_owner`; `src/pin/adapters/retention/deletion.py`: `finish_pending_deletions`, `replay_deletion_artifact`. Existing TR-03 retains only prior-trial/current-paid-coverage facts for its approved purposes. |
| Restore fence | `src/pin/platform/recovery.py`: `_arm`, `_release`, `require_recovery_ready`; `deploy/restore_snapshot.sh` replays deletions and leaves runtime fenced. An older snapshot can contain a grant revoked after that snapshot. |

## Shared contracts

### C1. Identity owns current consent

Add the smallest durable Identity-owned current-consent record and a published immutable snapshot/authorization capability through `identity/api.py`. Proposed logical fields are owner ID, state (`required`, `granted`, `revoked`), monotonically changing revision, policy version, unique grant ID, accepted-at time, accepted-language, acceptance inbox/update reference, and revoked-at time when applicable. Missing data means `required`. No message bodies, voice content, payment facts, IP-address evidence, or separate indefinite consent-event history are required.

A unique grant ID changes on every acceptance, including after deletion/return or recovery; revision alone cannot prevent reuse across a recreated account. An active authorization is the exact tuple `(trusted owner ID, grant ID, policy version)`, with current state `granted` and a policy version equal to the source-controlled current notice version. Unavailable storage, invalid state, absent metadata, and version mismatch deny AI work. A materially changed processing notice requires a new policy version and fresh acceptance; deployment/configuration cannot silently promote older acceptance.

Identity exposes owner-checked reads, grant, revoke, and verification of that tuple. Mutations use expected revisions and trusted Telegram actor/inbox identity. They run in the caller's unit of work, serialize concurrent changes, and are idempotent for a replay of the same accepted action. Its published final-authorization fence must also participate in the caller's short unit of work, serialize against grant/revoke/deletion, and retain that serialization through the protected effect or dispatch-marker commit. A read in another session, or a check followed by an unprotected commit, is insufficient. Cover the missing-record case without a grant-creation race. Task grooming must establish one consistent lock order across these operations, including existing lifecycle/job locks. Stale callbacks cannot overwrite a newer grant or withdrawal. Other contexts consume the published contract/ports; they do not query Identity tables or own a second consent flag. Billing access, financial receipts, and preferences versions retain their existing ownership.

No numeric consent retention/expiry is selected. The operational current record follows account deletion; this does not authorize retaining new consent evidence after deletion.

### C2. Explicit onboarding and existing-user actions

At the final initial-onboarding preview, display the existing preference summary and this concise notice in the selected language:

- EN: "Pin uses external AI services to interpret your text, voice transcripts, and relevant context from your plans. By choosing Agree and finish setup, you consent to this processing. You can withdraw consent in /privacy."
- RU: "Pin использует внешние ИИ-сервисы для обработки вашего текста, расшифровок голоса и релевантного контекста ваших планов. Нажимая «Согласиться и завершить настройку», вы соглашаетесь на эту обработку. Отозвать согласие можно в /privacy."

Show one explicit primary action, EN "Agree and finish setup" / RU "Согласиться и завершить настройку", plus Details and Cancel. Do not add a checkbox or a second consent confirmation step. The primary action atomically accepts the displayed current policy, saves the exact current preference draft, activates/restores access through the existing Billing contract, and completes onboarding. Failure rolls back that unit of work. Cancel grants nothing and preserves the existing trial/preference behavior.

Initial acceptance must use a fresh opaque callback binding owner, draft ID, draft revision, current consent revision, action, and displayed policy version. A plain yes/confirm, a historical Confirm button, an earlier draft, a foreign actor, or an old policy must not grant consent. Ask for the current explicit preview instead. Preferences-only confirmation keeps its existing meaning and cannot silently grant/regrant consent. Editing a language or time zone does not withdraw valid consent.

The Details button displays the full current bilingual privacy notice inside Telegram through the same deterministic rendering as `/privacy`; it must not require a website or AI call. Keep the active onboarding draft intact and provide a way to display its current preview again. Viewing details never accepts anything. The detailed notice names OpenRouter and the selected model-provider role, explains local voice transcription and possible external transcript processing, and retains the existing truthful retention/deletion/provider-copy limits and unresolved statutory-payment wording.

For an existing profile with missing, revoked, or obsolete consent, the next AI-required text/voice interaction returns a localized consent prompt with Details and explicit "Agree and enable AI" / "Согласиться и включить ИИ". `/privacy` provides the same acceptance entry and current status. This action records consent only; it does not restart onboarding/trial, edit preferences, buy anything, accept payment terms, or reset allowance. Tell the user to resend the request after acceptance. The denied request is completed once, not silently retained for later execution or submitted automatically when consent is granted.

### C3. Admission, provider egress, and result fencing

Preserve Telegram reception and required local inbox/idempotency records. Consent controls Pin's AI processing path; it is not a claim that Telegram has not already received the message.

Stamp AI-eligible inbound work with its current authorized grant tuple at admission, using the trusted owner mapping. Missing/legacy stamps are unconsented. Deterministic commands may still use the same inbox without a grant. Before context preparation, voice allowance reservation/download/transcription, resumed speech continuation, and external model work, verify the admitted tuple against current Identity truth. A grant issued later cannot authorize an earlier denied/unstamped input. A newly issued grant never revives older work under a different grant ID.

Check authorization again at each actual provider attempt, including repairs/retries and direct assistant entry points. Compose an Identity-backed authorizer at runtime through a port; a single Capture UI check or Billing allowance check is insufficient. Reuse the existing durable provider-call start record as the dispatch boundary: in the same short unit of work, acquire the C1 final-authorization fence, authorize the exact grant tuple, create its owner/job-fenced started-call marker, and commit while still serialized against withdrawal. Carry the tuple with that attempt. Denial before this work item's first dispatch causes zero total provider calls and no AI-turn consumption; release unused holds according to existing Billing semantics. Denial before a later repair/retry causes zero additional dispatches, suppresses obsolete output, and preserves accurate receipts for earlier started/unknown attempts. Apply the existing applicable Billing settlement/refund behavior independently of provider cost: a provider charge is not itself proof of a user AI-turn charge. Preserve existing unknown-usage and receipt-reconciliation rules. `with_allowance` and speech composition must retain the authorizer; no default-allow constructor path or model-smoke bypass is permitted.

Do not hold a SQL transaction over Telegram/provider I/O or local model inference. A committed, authorized dispatch marker defines an already-started attempt; withdrawal cannot retract that attempt. It may finish or time out after withdrawal. No subsequent repair/retry may acquire authorization under the old grant. Started/unknown attempts must not be blindly resubmitted after a crash.

At provider return, cached-result replay, transcript continuation, draft-result application, and AI-derived reply delivery, verify the same grant tuple. For every protected commit, use the C1 final-authorization fence in that same short unit of work, serialized through commit against grant/revoke/deletion: this includes persisting/applying an AI-derived result, proposal or proof; applying its Save/Confirm effect; and creating its Telegram reply dispatch-attempt marker. An earlier check cannot authorize a later unprotected effect. Either the effect/marker commits before withdrawal can commit and acknowledge, or withdrawal wins and the effect/marker is denied. No I/O or inference runs under this fence. If the tuple is no longer current, complete/terminalize the affected work safely, suppress the model output and its new proposal/preview, and send at most a static consent-status response. A new grant does not make an old result current. Keep necessary numeric provider-usage receipts even when output is suppressed; do not misreport an already-started attempt as never sent or erase unknown cost. Existing retention rules continue to govern previously stored content.

AI-derived drafts, clarifications, proposed-mutation previews, callback proofs, and queued replies carry the grant ID/policy provenance needed to reject obsolete work. An old AI-derived Save/Confirm cannot apply after withdrawal/regrant. Existing saved plans, already confirmed schedules, deterministic plan/status commands, and independently generated non-AI mutation previews retain their normal authorization rules. Consent is not a new blanket prerequisite for Planning writes or Notifications delivery.

### C4. Withdrawal and honest limits

`/privacy` shows current consent status and an explicit "Withdraw consent" / "Отозвать согласие" callback when granted. Bind it to the trusted owner and current consent revision/grant/policy. On valid use, persist `revoked`, change the revision, invalidate the active authorization, and acknowledge once. Repeated delivery cannot reinstate consent. Fresh acceptance always requires the current explicit notice and creates a new grant ID.

The effective point is the committed withdrawal transaction; acknowledge only after that commit. The C1/C3 shared-UoW fence orders each protected result/effect/dispatch-marker commit against withdrawal, so a worker that read granted cannot commit such an effect after withdrawal wins. Ordinary owner-ordered queues may delay handling; receipt of a command alone is not an acknowledgement that withdrawal already took effect. Do not promise instantaneous cancellation at Telegram arrival. New unstarted work after the effective point is denied; stale queued/replayed work remains denied after reacceptance. Workers check before progressing each expensive stage. In-flight local recognition is discarded on its next check; audio cleanup still runs.

The acknowledgement explains: future AI requests are blocked; a request already started may finish and data already transmitted cannot be recalled by Pin. Suppress its uncommitted results and pending AI-derived replies/proofs as described in C3. A Telegram send already authorized/started may complete; do not claim it was recalled. Revocation is not account deletion, subscription cancellation, payment authorization, a refund, or a promise that provider-held copies were erased. Existing saved-plan reminders and deterministic bot commands remain available under their existing access rules.

### C5. Deletion, return, migration, and recovery

The migration creates required consent storage without backfilling grants from profiles, payments, trial use, old confirmations, inboxes, or cached model results. Existing users therefore require explicit consent before their next AI request. Legacy queued AI inputs/results/draft proofs lacking grant provenance fail closed; ordinary reminders and non-AI commands keep working.

Deletion uses the existing lifecycle fence immediately and purges the Identity consent row, its operational proof/challenge references, and AI-derived queued work through existing context-owned purge composition. Include the new model in Identity's model registration/purge coverage. TR-03 return restores only its approved paid/trial facts, never consent, deleted plans, old callbacks, or old AI jobs. Return requires fresh explicit consent even if the canonical owner ID is reused. Existing deletion replay must preserve a legitimate later return generation while leaving its new consent dependent on that generation's fresh acceptance.

For restored snapshots, use the smallest conservative recovery rule: invalidate all restored active grants before the recovery fence can be released, and require fresh acceptance in the restored runtime. Mark them `required` and advance their revisions; do not call this a user withdrawal. Their old grant IDs cannot authorize work. This avoids inventing a separate retained consent ledger or claiming that an older grant proves no later withdrawal occurred. Wire the invalidation into recovery arming/release prerequisites in `platform/recovery.py`, idempotently, and invalidate restored grant-bound AI artifacts. A pre-consent snapshot migrated forward has no grants. If invalidation/reconciliation cannot be proved, keep AI/runtime recovery fenced; existing deletion/delivery/payment/usage recovery evidence remains required. Add truthful reacceptance wording to the recovery guidance.

Deploy the migration and all authorization consumers as one compatible bot release. Never run an old provider/speech worker that bypasses the new contract alongside the new consent UI. Rollback must keep AI workers fenced rather than resume an unguarded older runtime. This document authorizes no deployment, database migration execution, or restore.

## Acceptance and verification

All criteria require recorded evidence against the final changed source. No check below has been executed by this planning assignment.

| ID | Observable contract and meaningful proof |
|---|---|
| PC-01 | New RU/EN onboarding shows the notice and in-Telegram Details; only the bound explicit final action atomically saves preferences, records current-policy acceptance, and starts/restores access once. Restart resumes the same draft. Plain yes, cancellation, stale draft/policy, foreign actor, callback replay, and transactional failure do not create unauthorized grants or duplicate trials. |
| PC-02 | An existing profile/payment/trial without consent is denied on text and voice with zero provider, download/transcription, or AI-turn spend. `/privacy` and its Details work before a profile and during onboarding. Accepting preserves profile/access/quota and requires request resubmission. |
| PC-03 | Separate fake-transport cases prove denial before call one leaves the counter zero, while withdrawal after call one and before repair leaves it one with zero additional calls. Assert provider receipts/unknown cost independently from the existing applicable user-reservation settlement/refund behavior. The obsolete late result, cached replay, AI-derived Save/Confirm and pending reply are suppressed; regrant does not revive old-grant work. No real provider request is needed. |
| PC-04 | Durable native database cases prove admission/grant binding, concurrent grant/revoke/version checks, duplicate job/token processing, restarts, and voice-transcript replay; missing or unavailable consent storage fails closed. In separate sessions, barriers pause immediately after the final grant authorization and before effect/marker commit while withdrawal runs. For provider dispatch, AI-result/proposal/proof application, AI-derived Save/Confirm and reply-attempt authorization, prove either the protected commit precedes withdrawal acknowledgement or withdrawal wins and that commit is denied. Regrant never authorizes the old tuple. Exercise the actual ingress/application/speech/provider-attempt/delivery composition, not only mocked UI branches. |
| PC-05 | Deterministic `/help`, `/privacy`, `/support`, `/settings`, `/today`, `/week`, subscription/payment support, Billing receipts and cancellation, deletion, normal reminders, and deterministic owner-confirmed task actions keep their existing behavior without consent. None treats processing consent as payment consent or changes money. |
| PC-06 | Account deletion, tombstone replay, and TR-03 return remove old consent/proofs and require fresh acceptance without resetting trial/paid facts. An old-grant result cannot affect the returned generation. Migration from existing profiles grants nobody. |
| PC-07 | Restoring a snapshot containing a now-obsolete grant invalidates it before release; retries are idempotent; old queues/results/proofs cannot run; fresh post-recovery acceptance permits only newly admitted work. Existing deletion/delivery/payment/usage release prerequisites remain intact. |
| PC-08 | RU/EN privacy and withdrawal copy accurately distinguish local processing, external transmission, already-started work, deletion, provider copies, and unresolved financial retention. Telegram length/escaping, status/buttons, and plain-text fallback are checked. No website dependency is introduced. |

Reuse and extend the existing seams, including `tests/test_identity_onboarding_postgres.py`, `tests/test_telegram_ingress_postgres.py`, `tests/test_capture_postgres.py`, `tests/test_model_adapter.py`, `tests/test_retention_deletion_postgres.py`, the uncommitted `tests/test_privacy_presentation.py`, and `tests/test_payment_support_routes_postgres.py`. Add focused speech/delivery/recovery cases at their corresponding existing test seams after decomposition. Run migrations and native checks only in an explicitly owned disposable fixture; record zero-skip native results, cleanup, environment, and dirty-tree source boundary. A skipped native suite is not proof. This planner did not start a service or perform DB operations; any parent-run native verification requires its own current evidence and does not prove these future consent contracts.

## Task index and traceability

The independent review found shared-contract revision `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510` ready for decomposition. These navigation/traceability additions do not change C1–C5 or PC-01–PC-08. Individual task grooming and package review are pending; no task or issue has been published. Reconcile existing release/privacy/operations issue contracts before authorized publication.

| Task title | Intended outcome | Individual draft |
|---|---|---|
| Record explicit processing consent through Telegram onboarding and privacy controls | Durable Identity grant/fence and working explicit RU/EN controls | [Identity and onboarding issue #29](https://github.com/dystrophyless/pin/issues/29) |
| Enforce processing consent across AI work, withdrawal, deletion and recovery | One complete authorization boundary across all processing/effect/lifecycle paths | [Guarded processing issue #30](https://github.com/dystrophyless/pin/issues/30) |
| Prove the integrated Telegram processing-consent contract | Final-source integrated proof and bounded local verdict | [Integration gate issue #31](https://github.com/dystrophyless/pin/issues/31) |

| Shared criteria | Owning draft / consumers | Verification evidence |
|---|---|---|
| PC-01, PC-08; Identity/control portions of PC-02/04/05/06 | Identity/onboarding; integrated gate consumes | Not run for this increment |
| PC-02–PC-07 processing, concurrency, compatibility, deletion/restore | Guarded processing; Identity publishes its fence | Not run for this increment |
| PC-01–PC-08 complete chain and compatible final source | Integrated gate, after both implementation tasks | Not run for this increment |

## Decisions, blockers, and delivery

| Decision / gate | Provenance and disposition |
|---|---|
| Explicit onboarding consent; concise vendor-neutral summary; external text/transcript/plan context; OpenRouter in details; no separate checkbox; existing-user consent; revocability | Owner choices supplied in the dispatch. Implement within this contract after review. |
| In-Telegram details; tuple/revision fences; deny/resend behavior; conservative restored-grant invalidation | Proposed minimal engineering contract in this draft; review before decomposition. They do not create a retention period or claim legal sufficiency. |
| Applicable Kazakhstan financial/statutory record policy, without IP/TOO registration | Unresolved mandatory bot release/acceptance gate. Processing consent does not resolve it. Obtain an owner-approved applicable policy/source separately; no KGD request or invented period. Independent consent implementation may proceed after review. |
| Legal sufficiency of disclosure/consent and any applicable provider-account requirements | Not established by implementation or this plan. Preserve the existing release gates and distinguish functional consent evidence from legal approval. |
| Additional provider calls, real Stars, landing, deployment/restore, and final acceptance | Not authorized here; provider allowance exhausted, actual Stars removed, landing deferred. Use offline fakes and separately authorized disposable checks; leave unproved delivery/acceptance explicit. |

Delivery requires the affected specification/package review and meaningful implementation evidence against the final source, plus separately authorized release actions. Local correctness does not close unresolved financial/legal gates or imply the bot is deployed. No owner question is needed to choose a checkbox or payment behavior: those choices are already settled. The remaining substantive owner decision is the applicable financial-record policy and its acceptance evidence; infrastructure/native-test access is an execution blocker, not a product-policy answer.

## Review correspondence

The [independent specification review](SPEC-001_processing_consent_review.md) reviewed SHA-256 `54d466ddd618d90acd60d743e0048813157c83963f3b318171f87be308d65a0d` and requested changes on 2026-10-19. The independent bounded recheck resolved both findings and recorded shared-contract readiness for decomposition at SHA-256 `615621317edce703e58317fe38065509d5ace3bd4a86ded38eebdb14773fe510`. The task-index additions above are subsequent navigation changes; task/package readiness, implementation proof and product acceptance are not established by that specification verdict.

| Finding | Correction in this revision | Verification status |
|---|---|---|
| PCR-01 HIGH | C1/C3/C4 require the published final fence in each effect/marker UoW, serialized through commit with grant/revoke/deletion; PC-04 requires native read-to-commit withdrawal barriers at all affected boundaries. | Independent specification recheck resolved the finding; runtime proof pending. |
| PCR-02 MEDIUM | C3/PC-03 distinguish zero total calls before first dispatch from zero additional calls before later repairs; receipts/provider cost and existing user Billing outcomes remain separate. | Independent specification recheck resolved the finding; runtime proof pending. |
