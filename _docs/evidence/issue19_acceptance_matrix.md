# Issue 19 acceptance evidence matrix

This is a verification checklist for the authoritative [issue 19](https://github.com/dystrophyless/pin/issues/19), not a second task specification. Source: approved SPEC-001 section 11 and current GitHub task contract. Updated 2026-10-14. Status: incomplete; no task acceptance or whole MVP readiness is claimed.

| Contract | Required proof | Current evidence boundary |
|---|---|---|
| Trial and paid expiry at exact instant | Before/at/after injected clock, independently of notice failure | Existing Billing foundation; rerun at final compatible revision |
| Truthful 24h/1h notices, quiet hours | Native production producer/preparation/send path, delay through expiry, no invented remaining hour | Native notice authorization/composition partial; full send/retry/quiet composition pending |
| Queued send access recheck | Expiry after queue insertion/before attempt start; no external send | Existing delivery foundation plus final integration rerun required |
| Protected mutation/model/voice paths | Expiry during preview confirmation/inference; denied write and usage reconciliation | Foundation evidence alone insufficient after changed seams; integrated proof pending |
| Active quota-exhausted reminders | Current paid/trial period with zero allowance still permits actual reminder | Rendering unit test alone insufficient; actual delivery pending |
| Useful future reminder resumption | Multiple missed due times, event still future, current versions, coalescing | Notes resume adapter implemented; persisted mixed-target proof pending |
| One unresolved-past summary | Replay activation/restart, one episode per period; no autoreschedule | Executor wiring/proof pending |
| One late initial check | Past appointment, initial never confirmed; confirmed delivery starts one-hour timer, quiet defers | Resume wired; complete mixed recovery proof pending |
| Known failed retry then expiry then renewal | Definitive transient failure retained safely, resumed initial with bounded normal follow-up | Concrete zero-attempt defect identified; Executor correction/proof pending |
| No revival of closed or spent work | Completed/cancelled/exhausted/confirmed/unknown/blocked and spent nudge cases | Final negative fixtures pending |
| Dispatch generation fences | Old job/outcome cannot alter resumed intent, timers or new STARTED receipt | Native stale outcome/record CAS passed; full old-job/new-attempt race pending |
| Deterministic subscription command | Real Telegram application route, actual period/remaining quota, zero model call | Presentation units passed; native application route pending |
| Returning deleted Telegram identity | Operational Telegram/profile rows deleted, actual resolver/start restores canonical same active paid period/quota, no plans | Store-level return proof passed; actual ingress/onboarding proof pending |
| No new trial after deletion | Retained minimal prior-use record, no fabricated TTL/financial retention | Store-level foundation; full return fixture pending |
| Atomic event receipts and recovery | Crash/replay/rollback, no duplicated sends or summary | Six native partial cases; final composition and full frozen qualification pending |
| Migration and DDD | Fresh upgrade, downgrade/re-upgrade, zero drift, static boundaries | generation_g lifecycle/drift passed; final revision requires rerun |

All evidence must record command, date, source revision or dirty boundary, environment, artifact and limits. Partial six-test proof used fresh owned pin_issue19_lifecycle_20261014_resume_j before latest late-check wiring. Final acceptance requires the complete matrix, compatible frozen-source full suite with no silent skips, and agreed delivery conditions. Preserve failed fixtures and historical pin_model_adapter_20261013. No additional live OpenRouter spend is authorized. Deployment/host qualification remain deferred by owner instruction; do not erase their separate issue gates.

## Orchestrator source audit finding: delayed failure event

2026-10-14, dirty worktree: `record_delivery_attempt_result` records a retryable failure immediately; expiry may subsequently mark the same intent skipped/access_expired. A later `apply_delivery_outcome` for that failure can overwrite its suppression state to failed while advancing last_outcome_attempt. Renewal currently requires skipped/access_expired, so this order can lose an otherwise safe resumed initial check. This is a source-based finding, not yet reproduced runtime evidence. Routed to the owning Executor for a fail -> expiry skip -> delayed event consumption -> renewal PostgreSQL fixture and correction preserving access suppression. Do not accept the marker fix from a fixture that bypasses durable event consumption.

Executor correction now exists in the dirty source: late retryable failure keeps skipped/access_expired state; marker is consumed on rearm and cleared before a new send. Two native tests were added for delayed failure replay and one summary per activation period. Root inspected these source changes; execution results and complete production-path coverage are still pending. This updates the finding to corrected-source/unverified-runtime, not accepted.

## Independent partial runtime rerun

2026-10-14, Orchestrator executed `uv run --offline pytest tests/test_issue19_lifecycle_runtime_postgres.py -q` using PIN_TEST_ISSUE19_LIFECYCLE_DATABASE_URL and fresh guarded pin_issue19_lifecycle_20261014_parent_k: 8 passed in6.28s, exit0. Exact cluster/user/version/name absence verified before creation. No fixture reset/drop. Dirty source after94273c8; concurrent Executor may add later changes, so this is a partial checkpoint, not final frozen qualification. Includes delayed retryable outcome/expiry/rearm and one-summary-job dedup tests. It does not prove mixed-target external delivery, all return/command paths, or all issue19 criteria.

Independent Orchestrator rerun,2026-10-14: fresh guarded pin_issue19_lifecycle_20261014_parent_l, `uv run --offline pytest tests/test_issue19_lifecycle_runtime_postgres.py -q`:13 passed in7.10s, exit0. Includes actual ingress/start and subscription route, persisted transport failure/expiry/outcome/renewal/one-nudge, and mixed persisted future/past target rearm. Dirty source following94273c8; final same-revision full qualification and complete AC audit still required.

## Stable implementation checkpoint

Source checkpoint: commit ee7eda5. Root independently ran the lifecycle file13/13 on parent_l before the commit; Executor reports13/13 on executor_g plus34 focused cases, full static checks and migration roundtrip/drift on migration_h. The committed suite now covers actual Telegram resolver/start restoration, deterministic subscription routing with a no-model fake, persisted mixed-target rearm, real Accountability delivery failure and expiry suppression, delayed outcome consumption, generation2 renewal, confirmed one-hour timer and scheduler replay, and terminal retry-marker clearing. Refer to tests/test_issue19_lifecycle_runtime_postgres.py and tests/test_reply_preparation_postgres.py for actual scenario boundaries.

Final frozen qualification is assigned to GPT-6 Luna High /root/qualify_access_revision, packet C:/Users/roman/AppData/Local/Temp/pin-parent-access-ee7eda5-20261014. It owns temp artifacts only. No final qualification PASS, issue acceptance or closure is recorded yet. Root must compare packet/source tree, fixture inventory, all failure/error/skip counts, migration/speech facts, complete contract coverage and cleanup before acceptance. Partial earlier findings remain historical entries rather than current verdicts.

## Frozen qualification failure — authoritative current verdict

Root inspected machine receipts for frozen ee7eda5b9a300db78813bf4c71421fa0423c1a74 in pin-parent-access-ee7eda5-20261014. Pytest:485 cases,476 passed,9 failed,0 errors,0 skips; exit1, overallFAIL. Eight failures are in tests/test_accountability_runtime_postgres.py; one is test_private_onboarding_survives_restarts_and_activates_one_trial. Partial13-case success does not supersede these regressions. Exact failed names and raw stack traces are retained in pytest.log and pytest.xml. Same Executor owns diagnosis/correction; no test deletions or weakened assertions are authorized.

Independent receipt facts: import provenance, both premigration steps, Ruff, mypy179, architecture and migration lifecycle/drift passed. Actual offline RU/EN speech passed with silence rejection per speech-result.json. These component results do not make the overall qualification green. New revision and separate fresh packet required after corrections; preserve this failure evidence. No issue19 acceptance/closure.
