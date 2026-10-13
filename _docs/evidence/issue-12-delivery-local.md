# Issue #12 — local delivery acceptance evidence

Date: 2026-10-14, Asia/Qyzylorda. Direct Orchestrator acceptance for Windows local development only. Source: `4d0cd8630a292a0d659eabe3f44038695cecebc7` (delivery implementation `c674a258ef5aac15a61cae8183f16f137abacf82` plus trailing-whitespace correction). No push or deployment.

## Exact-source verification

The source was exported with `git archive` to `C:\Users\roman\AppData\Local\Temp\pin-parent-delivery-20261014-12ac\source`. Active capture schema/DTO/router changes were excluded from the index and source snapshot while preserved in the working tree. Python import provenance points to that exported source.

Environment: Windows 11 build 26200, AMD64, CPython 3.12.12, PostgreSQL 18.6 on the owned disposable local cluster at port 51017. Thirteen fresh manifest-scoped databases were provisioned after the exact temporary data-directory check.

| Check | Observed result |
|---|---|
| Full `pytest -q`, JUnit XML | **263 passed, zero skipped**, 110.10s; skip absence independently asserted from XML |
| `ruff check .` | PASS |
| `mypy src` | PASS, 115 source files |
| `python -m pin.architecture` | PASS |
| Alembic upgrade/current/check, downgrade base, re-upgrade/check | PASS; single head `delivery_recovery_summary_0001`; no schema drift before or after |
| Healthy actual outer-loop invocation profile | 50 simultaneous due owners; actual transport-dispatch-start p95 **2.000s**, within the local <=10s target |
| Separate pump/claim microbenchmark | p95 **0.716s**; queue-only metric, not dispatch-start proof |

The load profile uses an injected fault-free transport and real PostgreSQL, eligibility checks, leases and attempt receipts. It measures time to transport invocation. It does not qualify Telegram rate limits, deployed hardware or network-delivery latency. The worker additionally passed its 30-case delivery/transport subset twice on the same isolated fixture before parent verification; the parent full exact-source run is authoritative.

## Scope and invariants proved

- Dedicated durable delivery-role loop handles reminder intents and generic bot reply outbox envelopes without holding SQL transactions during Telegram sends. Current bot/private-owner mapping is required; onboarding may reply before an access account exists, expired accounts may receive generic replies, deleted accounts cannot receive stale private replies.
- Reminder authorization checks current occurrence/plan/series/preference and dispatch versions, active entitlement, due/useful event time, quiet policy and a live lease. Exhausted active AI quota does not suppress reminders. Stopped series still permit qualified already-generated open occurrence reminders.
- Current-state changes committed before authorization prevent obsolete sends. Preference/Planning/Billing row locks serialize changes through durable attempt-start. Tests pause around this boundary for completion/cancellation, deletion and period mutation; naturally elapsed entitlement or lease expiry prevents authorization. Changes after the attempt starts may still leave an in-flight message that arrives; no retraction or exactly-once promise is made.
- Confirmed, known failed, blocked, deferred/coalesced, skipped and unknown attempts stay distinguishable. 429/known transient retry is bounded; known rejection and blocked destinations are handled separately. Crash/timeout after send-start recovers as unknown and cannot automatically resend or fall back. Stale leases cannot start or finalize replacement attempts. Typed outcome events preserve intent/target/dispatch version and optional cycle linkage for the later accountability consumer.
- Useful late advance reminders coalesce; already-started/expired advance reminders skip. Default quiet policy defers only while useful; explicit priority records conflicts. A newly approved dispatch version clears stale deferred eligibility time.
- Recovery summaries deduplicate a caller-supplied stable owner/episode key. They read current unresolved past Planning rows, including untimed and zero-reminder work, exclude foreign/terminal/ongoing appointments, and lock bounded membership/current state through authorization. Summary text uses plain Telegram mode to keep untrusted titles literal. Summaries create no completion cycle, follow-up or automatic reschedule; later renewal/outage coordination owns triggers and accountability owns the approved late-check campaign.

## Real Telegram proof

The exact source was exercised through `process_delivery_iteration` and `TelegramReminderTransport` using the already-authorized dedicated private bot. Two actual Telegram API responses acknowledged **RU message 17** and **EN message 18**. Returned private-chat message text exactly matched each request, including literal `*...*` and `[test]` text; parse mode was None. Both corresponding SQL attempts were `confirmed` with matching provider message IDs, even though their synthetic active owners had exhausted AI quota.

Raw structured proof is in `telegram-live.json` within the packet. Credentials/chat IDs are not published. These are real API-confirmed messages, not artificial acknowledgements; API success does not establish human reading. Synthetic isolated test plans/access were used, without payment provisioning or model calls.

## Cleanup and limits

All thirteen parent proof databases, the separate live smoke database, the stopped delivery worker fixture and the stopped recovery-lock worker fixture were dropped after exact-cluster/no-active-connection checks; absence was verified. Active capture fixtures, intentionally retained older prototypes, historical model receipts and unrelated databases were preserved. Packet files include source, revision/environment/manifests, check logs, JUnit XML, migration result and Telegram/cleanup facts.

No implementation Reviewer was used. No new OpenRouter calls occurred; the earlier eight-call/$0.01 smoke window remains exhausted. Final accountability cycle wiring, renewal/outage triggers, deletion orchestration and end-to-end gates remain later issues. Docker, Linux/hosted CI, deployment/actual-host qualification, broader provider quality and full MVP/release acceptance remain deferred. This evidence accepts #12's local increment, not the complete MVP.
