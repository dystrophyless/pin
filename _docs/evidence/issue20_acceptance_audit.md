# Issue20 РІР‚вЂќ Orchestrator acceptance audit

Authoritative task: https://github.com/dystrophyless/pin/issues/20. Approved SPEC-001 section11. Current state: incomplete, local dirty implementation after c166b6a; no acceptance or provider qualification claimed.

## Root findings РІР‚вЂќ 2026-10-14

Independent `uv run --offline mypy --no-incremental src/pin` returned8errors in4files (184fileschecked). Crucial adapter mismatch: Bot.send_invoice does not accept subscription_period in the pinned aiogram contract. The recurring30day flow must use a supported official method, with actual SDK shape verification rather than a permissive fake. Other typing findings affect nullable Telegram text and command replies plus refund session types.

Pre-checkout currently has a6second outbound request timeout but an unbounded preceding DB/owner-lock transaction. A whole path deadline must cover persist-first ingress, lock waits and API answer, with a failed/rejected checkout under contention and no granted access. Provider elapsed time cannot be guaranteed solely by HTTP timeout. Verify immediate gateway route, post-commit provider call and durable update/decision state under delay.

These findings were routed to the same LunaHighExecutor. No separate implementation Reviewer. No root source changes. The payment operations note belongs at `_docs/payments.md`.

## Required acceptance evidence

| Contract | Required evidence |
|---|---|
| Accepted terms/order/overlap | Actual private command route; no invoice or access without owner-bound current terms; trial upgrade and overlapping queued/active paid period rejected |
| Recurring Stars invoice | Current official provider rules, pinned SDK compatible method,250XTR/2592000seconds/emptytoken/opaquepayload; local injected proof distinguished from real Telegram test environment |
| Pre-checkout | Authenticated persist-first ingress, complete bounded decision/answer timing, invalid owner/currency/amount/order rejected, approval grants nothing |
| Receipt/renewal | Authenticated confirmed provider fields, unique charge/period, concurrent duplicates and out-of-order transitions; fresh paid quota exactly once |
| Cancellation | Actual command/provider args, confirmed future-renewal stop only; paid-through and remaining quota retained; unknown/failed/stale attempt reconciliation |
| Refund | Reviewed operator intent, external call outside transaction, separate coverage survives, receipt replay never resurrects refunded period, receipt/refund lock concurrency |
| Support/terms | RU/EN routes and manual procedure, truthful configured support destination; financial-retention applicability separate prelaunch gate |
| Reliability | Crash/unknown external effects never blindly resent; lease/token fence; native PostgreSQL SQL facts rather than only fake reports |
| Delivery | Frozen-source all-fixture suite, no silent skips,static/migrationdrift/roundtrip; exact revision and raw artifacts |
| External limits | No live charges/refunds/cancellation performed; successful authorized Telegram payment test environment not yet proven; do not accept whole issue from local fakes |

Executor reported initial domain14PASS then focused20PASS and native4PASS on owned pin_issue20_payments_20261014_f8498bfc. These are partial earlier proofs before the root-found adapter/deadline correction, not a current acceptance verdict. Exploratory failure DBs are preserved. No invented provider sandbox, legal retention TTL or financial access permission.

## Corrected source checkpoint55fcbfc

Root inspected the revised pre-checkout deadline/DB timeout implementation and independently ran mypy184files PASS, Ruff fullsource/tests/migrations PASS, and27focused domain/Telegram cases PASS6.27s. Implementation commit55fcbfca5b2315c5738622a546e9796ecd3c2e37 uses typed createInvoiceLink with subscription_period rather than unsupported sendInvoice kwargs. Native Executor5-case proof includes persisted update/order-lock contention and failed approval without paid period. This corrects the source findings; final frozen full-suite and complete provider-path acceptance remain pending.

Current provider documentation independently checked via Context7 and official Telegram pages2026-10-14: https://core.telegram.org/bots/payments-stars#testing-payments and https://core.telegram.org/bots/features#creating-a-bot-in-the-test-environment. Stars can be tested in a separate test environment; it requires separate user/bot credentials and requests use /bot<token>/test/METHOD_NAME. The ordinary dedicated development bot is not proof of test-server access. Root asked the owner whether a test-server account/bot exists; response pending. No credentials printed and no Stars charge/refund attempted. Actual authorized payment-environment evidence is still required by issue20, not replaced by injected local tests.

Frozen qualification packet pin-parent-payments-55fcbfc-20261014 is running with31opt-in PostgreSQL fixtures and known source provenance; migration lifecycle/drift passed per Executor report. No full PASS or issue closure claimed until raw receipts are complete and inspected.

## Frozen55fcbfc CPU-spin finding

Root captured a read-only py-spy dump of actual pytestPID3344: active main-thread stack at FailingAckBot.get_updates in test_telegram_ingress_postgres.py765, called by poll_updates64 in test_polling_callback_ack_failure_cannot_hold_later_payment_update_or_offset826. The fake supplies callback+precheckout, lacks answer_pre_checkout_query, then returns empty updates without yielding; the production handler records unknown API outcome and retains the payment offset, while fake stop requires an unreachable advanced offset. This establishes an infinite fixture loop, not a slow speech stage or inferred failure from elapsed time. Root used ephemeral uvx py-spy with no repository dependency changes and no local-variable/credential output.

Qualification Executor was authorized to interrupt only this diagnosed spin, preserve frozen source/logs and record interrupted/FAIL (not full PASS), then finish safe remaining component checks and scoped cleanup. PID3344 is reported gone. Actual callback-ack/offset assertions remain required; same product Executor owns fixing strict precheckout fake contract and bounded/yielding fake calls on fresh native fixtures. No test skips, production recovery-policy weakening or rewriting failed packet is authorized. New fixed revision needs a separate full frozen qualification.

## Test DC recurring-period finding — 2026-10-14

The authorized Computer Use flow completed test-bot onboarding, terms acceptance and /buy. The guarded /test/createInvoiceLink request with the production contract (250 XTR, subscription_period=2592000) failed with SUBSCRIPTION_PERIOD_INVALID. No successful payment, paid period, cancellation or refund was produced. Sanitized diagnostic is retained at C:/Users/roman/AppData/Local/Temp/pin-issue20-sandbox-proof/invoice-link-diagnostic.json.

Telegram TDLib maintainer levlam explicitly states that Test DC subscription periods must be 60 or 300 seconds: https://github.com/tdlib/td/issues/3309. A temporary test-only 300-second harness is being prepared; this is accelerated provider integration evidence and cannot claim to prove the actual 30-day interval. Production period remains 2592000 seconds. Main-server financial activity is not authorized.

Frozen revision24199e2 qualification reported 510 cases:508 passed,2 failed,0 errors/skips,236.51 seconds. Both failures concern paid-user plan cancellation in test_mutation_delivery_postgres.py: the bare /cancel route responds with billing cancellation text instead of a task mutation preview. Ruff, mypy184files and architecture reported PASS; raw packet and remaining qualification checks are retained separately. Whole-issue acceptance remains pending routing reconciliation, a fresh full passing packet and authorized provider proof.

## Current 42e4f38 qualification and Test DC evidence

Frozen 42e4f3810c1e63faab05d8ad27542cf343583459 full suite completed with 508 passing and 2 failing cases, zero errors/skips, 328.67 seconds. Both prior task-cancellation failures pass. New failures are precheckout lock-timeout denial (the artificially shortened 0.6-second deadline exhausted before the fake answer) and actual 50-owner transport-dispatch p95 of 11.935 seconds against the unchanged 10-second limit. Root inspected raw JUnit traces. These failures require diagnosis, not threshold relaxation or a passing claim. Packet: C:/Users/roman/AppData/Local/Temp/pin-parent-payments-42e4f38-20261014. Remaining static/speech/cleanup evidence is being completed by the Qualification Executor.

The authorized accelerated Test DC flow produced one sent/open 250-XTR invoice, with a process-local 300-second policy. Read-only guarded snapshot of pin_issue20_sandbox_20261014_33a6ed3b found nine persisted Telegram updates (five callbacks/four messages), zero root pre_checkout_query updates, zero message.successful_payment updates, zero receipts and zero paid periods. This describes the persisted inbox; it cannot rule out a query rejected before persistence. Snapshot: C:/Users/roman/AppData/Local/Temp/pin-issue20-sandbox-proof/test-dc-accelerated-run-snapshot.json.

The owner supplied a screenshot of Checkout (Test), Telegram Stars Top-Up, merchant izpremiumbot, with the message that the bot did not respond in time and the card was not billed. That is the separate top-up merchant stage, not a confirmed Pin checkout. The Test DC account balance stayed zero. Telegram Stars docs require the relevant invoice merchant to answer precheckout within ten seconds and fulfillment only after successful_payment: https://core.telegram.org/bots/payments-stars. Historical Desktop issue29956 reports test-Star issuance restrictions, but does not prove every current test path unavailable. Actual Pin provider payment, cancellation and refund remain unverified. No real financial activity is authorized; an owner decision remains pending.

## Corrective source fe5ce2e

Root inspected the four-file source/test diff and the fresh payment JUnit: 7 tests, zero failures/errors/skips (6.968 seconds XML suite; 7.11 seconds console). The delivery suite reports 30 passing cases; the unchanged actual 50-owner p95 check records 2.064 seconds. Source fe5ce2e uses transaction-local parameterized PostgreSQL lock_timeout inside the remaining checkout decision budget, preserving the outer deadline and API answer reserve. Ready delivery work is claimed before global due sweeps; sweeps still run after ready sends or on idle, and Billing cancellation/invoice priority remains intact. Added actual webhook lock-contention persist/deny proof and a regression proving the due sweep runs after ready transport dispatch. No threshold was relaxed. The initial reused-fixture payment failure is preserved separately; the new guarded fixture pair provided the passing file evidence.

A new full frozen qualification of fe5ce2e is running at C:/Users/roman/AppData/Local/Temp/pin-parent-payments-fe5ce2e-20261014. Focused PASS does not establish full qualification or provider acceptance. The accelerated Test DC invoice remains uncharged; no external provider payment/cancellation/refund proof has been added.

## Frozen fe5ce2e final result — resumed 2026-10-15

The quota interruption did not cancel the already-running qualification. Root checked process state and terminal JUnit/logs before resuming missing stages. Full pytest: 512 tests, 511 passed, one failure, zero errors/skips, 263.264 seconds. Former checkout lock denial and cancellation regressions passed; actual dispatch-start p95 was 2.558 seconds. Ruff, mypy184, architecture, migration roundtrip/drift and actual offline RU/EN/silence passed. Exactly 31 manifest-owned databases were dropped and verified absent; both sandbox databases and historical pin_model_adapter_20261013 were preserved. Commit fe5ce2e5677cf6d911322a52ece078b751e56f63; tree49cfda08a8da7e49ae2ae75208b330aea1f79924; archive SHA256 c70a629706b74f6bef9b44a3406708264eb5eb02af589f01e755655fab3c0720,382files.

The remaining failure is test_accountability_callback_previews_then_confirmation_closes_cycle: after a confirmed initial send, preview and confirmed completion, the cycle is completed and nudge_due_at is cleared, but initial_confirmed_at remains set. Same Executor is checking that assertion against the approved historical-delivery fact and terminal future-work contract; no business rule is waived. This qualification remains FAIL, not overall issue20 acceptance.
