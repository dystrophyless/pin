# Pin handoff


## Latest current-source local qualification — 2026-10-17

Frozen source `9c474cf` passed **569 tests, zero failures/errors/skips**, with static, migration, landing and cached offline speech checks passing. [Current evidence](evidence/local-integration-9c474cf.md). Root independently checked raw JUnit, all478 tracked Git/frozen-source hashes, and unchanged post-cleanup catalog. The prior `d0972d3` full run failed two stale-test-clock cases and one style check; its report is retained. The clock correction preserves the no-extra-replies assertion. Older557/551/549 full-run counts below are historical, not the current qualification.

This is local regression evidence only; it does not establish hosted operation, legal compliance, model quality or overall acceptance. No new provider/Telegram/Stars calls were made in qualification. AWS staging is deployed and the deployment/backup workflows are green; bounded runtime, security, S3 checksum/download and RU/EN temporary HTTP landing checks passed. A separate host-side disposable restore passed, but S3-based restore, full recovery, measured RPO/RTO, hosted alerts, applicable legal financial-retention evidence and complete integration/owner acceptance remain outstanding. See [AWS initial deployment evidence](evidence/aws-initial-deployment.md). The 99%/400-request/model-host economics benchmark is removed from required acceptance by SPEC-001 v0.18, with no quality or capacity PASS claimed.

Updated:2026-10-17

## Repository and authority

- Actual checkout:`D:\Program\python\pin`; branch`main`; origin`https://github.com/dystrophyless/pin.git`.
- Qualified product source:`be53f926d6320394e49e5986a77d13136b591949`. Later pushed changes through`de28299` are documentation only; inspect Git for later commits.
- Read [AGENTS.md](../AGENTS.md), [SDD workflow](sdd/README.md), [orchestration](orchestration.md), [brainstorm](PROJECT_BRAINSTORM.md), [SPEC-001v0.18](specifications/SPEC-001_pin_mvp.md), [architecture](ARCHITECTURE.md) and [task index](specifications/SPEC-001_pin_mvp_tasks.md).
- Standing objective:implement and complete remaining issues. The owner authorized local implementation, direct criterion verification/eligible acceptance and closure, GitHub updates, commits and pushes. Preserve the original whole-task requirements; local evidence does not waive remaining gates.
- AWS deployment is in progress: the first staging stack and backup workflow are green; bounded runtime, firewall/secret, S3 download/checksum and RU/EN temporary HTTP landing checks passed. A separate host-side disposable restore passed but is not an S3 restore or full recovery proof. See [AWS initial deployment evidence](evidence/aws-initial-deployment.md). Measured RPO/RTO and recovery reconciliation remain outstanding. The 99%/400-request/model-host economics evaluation is removed from required acceptance by SPEC-001 v0.18, not deferred.
- Use project-local Ponytail full, never Superpowers. Exact GPT-6 Luna Executors/Publishers with explicit effort; no implementation Reviewer. Root verifies AC and raw evidence directly. Let active executors work without repeated nudges.
- Preserve unrelated untracked`output/`mascot material. Stage explicit in-scope paths only.

Actual Stars transaction testing is **removed from acceptance, not deferred** (owner:"Проверку оплаты не откладываем, а убираем."). Preserve payment functionality and clearly labelled local synthetic-event/PostgreSQL checks. No Test Server top-up, checkout, actual receipt/refund attempt or request for real money. Other legal/account/provider/host requirements remain.

Landing CTA and support are both`https://t.me/pin_appbot`; support commands`/support`and`/paysupport`. Kazakhstan is the intended operating/revenue jurisdiction; entity form (IP/TOO), tax applicability and statutory financial-retention schedule remain unvalidated. Never invent a retention TTL for financial records or trial markers.

## Latest verified local qualification

[Exactbe53f92qualification](evidence/local-integration-be53f92.md):**557passed,0failures/errors/skips**, JUnit282.436s (console282.47s,5warnings). Includes receipt-clock propagation and the fourth operations projection case, plus core/support/paid/recurring integrations. Ruffsrc/tests/landing, mypy219, architecture/import provenance, migration roundtrip/drift, landing6/node/validator and cached offline WhisperRU/EN/silence pass. No external provider/Telegram/payment/download/deployment call occurred.

Tree`0fa75dcdeab0ea296fb15a341afb3e9f4a150021`; Git archiveSHA256`1694714f06c2402ff48d269bf4c0786a5d22df77c4dc94f946ab0edee9d72247`. Root independently recreated the archive and verified all463committed/extracted file hashes before/after execution, rawJUnit/stage logs, fixture idle/ownership receipts and live exact174catalog records. All37fresh manifested databases are absent, protected3present. Packet:`C:/Users/roman/AppData/Local/Temp/pin-final-frozen-local-qual-be53f92-20261016`.

The50-owner actual outer-loop injected-transport dispatch-start p95 is2.174s against10s. The frozen test emits only aggregate p95; individual delays were not retained and cannot be independently recomputed. Root inspected the50-sample/nearest-rank calculation and asserts. This is local aggregate evidence, not target-host capacity or TR-04 quality.

Historical packets stay unchanged:[2b03278PASS551](evidence/local-integration-2b03278.md),[993412dPASS549](evidence/local-integration-993412d.md),[12aa6b5FAIL547/549](evidence/local-integration-12aa6b5.md). Initial inventory-helper failures and archive deprecation warning are retained in the latest packet; do not rewrite them as product failures or erase them.

[Receipt-clock focused proof](evidence/issue-25-reference-clock-local.md) atbe53f92:81offline/2native tests plus root5newcases, all19source/test hashes and exact174catalog verification. Polling/webhook observed UTC arrival is persisted once and carried through queued text/prepared voice/retries; elapsed duration is added in UTC before event-zone conversion. Billing/access use current processing time; payment deadlines use their monotonic clock. Adapter observations do not establish Telegram upstream or precise network arrival. Fake interpretation proves outbound context, not live-model comprehension.

Other detailed proof:[core lifecycle](evidence/issue-25-core-local-integration.md),[recurring stale-version correction](evidence/issue-25-recurring-local-integration.md),[paid lifecycle](evidence/issue-26-paid-local-integration.md),[operations context projection](evidence/issue-22-context-faults-local.md),[landing destination](evidence/issue-21-bot-target-local.md),[retention](evidence/issue-23-retention-local.md). All relevant tests now occur in the557-case frozen run; seeded projections do not prove producer/host faults.

## Current decisions, pending input and remaining gates

Issues 1-20 are accepted/closed for Windows local development; issues 21-26 and #28 remain open under their remaining gates. GitHub is synchronized: #24 is in progress; #27 is closed as not planned because v0.18 removed its evaluation from required acceptance; #28 no longer depends on #27. No overall MVP acceptance follows.

Historical TR-04 scoring decision: the owner previously approved the 99% threshold and separate 100-request groups. SPEC-001 v0.18 supersedes that plan as required acceptance: no 99% score or mandatory corpus is required, and no quality PASS is claimed. Existing two-clip RU/EN smoke and prior live/offline observations remain limited evidence only; further evaluation is optional, nonblocking and needs separate authorization.

Historical candidate manifest and coverage remain optional artifacts: the previously approved corpus/reference set is preserved for provenance but is not a mandatory evaluation. The completed two-clip RU/EN smoke is not a broad quality or capacity benchmark. No additional model/API spend or evaluation is authorized by this scope amendment.

The TR-04 draft protocol is retained as optional historical planning material. Corpus collection, quality scoring and actual-host/provider capacity/economics benchmarking are no longer MVP acceptance gates under SPEC-001 v0.18; no pass is implied.

Executor `/root/final_frozen_local_qualification` completed the Issue22actualproducer→diagnostics test in `tests/test_operations_postgres.py`:focused5/5pass,JUnit12.184s, no failures/errors/skips. Root reviewed the real producer/fake-external-boundary composition, testhash/all219sourcehashes, independent Ruff/mypy and exact live174catalog cleanup. [Evidence](evidence/issue-22-produced-faults-local.md). This is a separate later focused result, not558full-suite passes; product source is unchanged. Original clock-harness failure preserved. No active ops writer remains. Root owns integration/Git; Publisher completed557-case updates and SPECv0.14reference-approval updates with AC/dependencies/openblocked statuses preserved.

Executor `/root/paid_local_integration` completed the actual local dump/restore rehearsal:focused1/1pass,JUnit53.929s,0failures/errors/skips, independentstaticchecks. [Evidence](evidence/issue-23-native-restore-local.md); packet`C:/Users/roman/AppData/Local/Temp/pin_issue23_restore_20261016f7ad49b1`. ActualPG18.6dump/restore and retentionCLI replaytwice pass; missing/malformedartifacts failclosed. Deletedplan staysabsent, retainedtrial/paid/billingidentity and unrelatedownerplan survive; actualworker marks restoredpre-snapshotstartedattemptunknown without anotherfake send. Root verified rawJUnit/testhash/dumphash/prepostcatalogbytes and live exact174name/owner baseline/protected3/bothfixturesabsent. Both exactroot-approvedfixtures were dropped afterownership/idlechecks; no native writer remains. This does not cover attempts absentfrombackup/allpost-backup reconciliation/automaticstartupfence/host/offhost/encryption/RPO/RTO. Source is unchanged;557full remains the latest fullrun, with5ops+1restore focused results separately.

Remaining required scope includes complete current live Telegram integration, hosted alerts/off-host recovery, applicable Kazakhstan financial-retention evidence and final owner acceptance. The quality target/corpus/host-model economics benchmark is removed from acceptance and must not be reported as passed. AWS deployment is in progress; see [initial deployment evidence](evidence/aws-initial-deployment.md). GitHub status is synchronized: #24 is in progress, #27 is closed as not planned without a qualification PASS, and #28 no longer depends on #27.

## Native environment and safety

- PostgreSQL18.6/version180006 at127.0.0.1:51017, rolepin_test, maintenanceDBpostgres; binaries`D:/Program/postgresql/bin`.
- Exact data directory:`C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013`. Baseline174non-template databases:preserve all historical/unowned databases.
- Protected:`pin_model_adapter_20261013`,`pin_issue20_sandbox_20261014_25eb0b51`,`pin_issue20_sandbox_20261014_33a6ed3b`. Never reuse`pin_issue18_prepared_20261014_98de`(known queue interference).
- BeforeCREATEverify endpoint/role/version/directory, unique fresh manifest ownership and absence. BeforeDROPverify identity/ownership/zero sessions; drop only exact manifested names. Verify absence plus unchanged nonmanifest catalog/protected databases with independent audit. Never reset fixtures to conceal interference.
- An observer timeout is not process termination. Inspect actual live handles/readiness before any restart.
- Credentials remain only in ignored`.env.local`; never print tokens/chatIDs/full environment or secret URLs. Main/TestServercredentials stay separate; no bot runner is assumed active.
- Cached offline Whisper is allowed; no dependency/model downloads or new external model calls.

Keep this checkpoint concise/current. Issue contracts are authoritative for scope/AC/dependencies; accepted changes belong inCHANGELOG. Record revision/environment/commands/artifacts and evidence limits; preserve other agents' work.

Latest voice checkpoint:the owner chose to pass each user's persisted RU/EN profile language into local Whisper. SPEC-001v0.17 records the decision; a missing/invalid selection fails before inference and refunds the voice reservation. The [manual smoke](qualification/TR-04_manual_smoke.md) preserves the original automatic-English detection failure and records both selected-language transcriptions from the same owner clips. Focused native PostgreSQL worker checks passed5/5 (RU/EN recognition, missing-profile refund/no inference, durable transcript recovery, and recognition failure/refund); Ruff and mypy passed. This is not the557-case suite, a99% quality result, provider/Telegram journey, or full TR-04 qualification. No200/400 collection/run; prior provider budget remains exhausted.

Latest TR-04 interpretation checkpoint:the two explicitly authorized owner-transcript calls both failed qualification—RU was schema-valid but added an unrequested one-day-before reminder; EN was rejected for an invalid `calendar_day`/`hour` reminder and included an invented appointment duration/fold. The corrected original RU transcript is Cyrillic; the earlier U+FFFD terminal-rendering report was mistaken. No further model calls were made. The local [smoke record](qualification/TR-04_manual_smoke.md) has the exact receipts/costs. Offline-only production-prompt grounding now keeps task date/time separate from before-event reminders, uses timed-task semantics for ordinary actions, and leaves omitted reminders to profile defaults; validator behavior is unchanged. Focused adapter tests and unchanged bounded-preview cost/tier check pass. Prompt behavior is not live-retested. Repo-local issue index still lists #27 open; no live GitHub read or mutation was performed.
