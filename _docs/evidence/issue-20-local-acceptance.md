# Issue 20 local acceptance audit

Date: 2026-10-15. Scope: Windows local development under the owner amendment in SPEC-001 section 11 removing actual Stars payment testing from acceptance. This is not deployment, legal approval or proof of actual provider operation.

## Requirement evidence

Product source remains unchanged from qualified `5464eec0f958c7bf40febf3dfe77ba0e5ed50fe5`. The frozen [combined qualification](local-integration-5464eec.md) passed 543 tests with zero failures, errors or skips, plus static, migration, offline speech and cleanup checks. The Orchestrator re-read the raw JUnit and the following relevant assertions rather than inferring payment correctness from the suite total:

| Required result | Local evidence in the frozen suite |
| --- | --- |
| 250 XTR / 30-day recurring invoice; checkout alone grants nothing | `test_invoice_adapter_creates_recurring_stars_link_and_sends_pay_button`; `test_precheckout_requires_exact_stars_terms_and_no_active_paid_period`; `test_invalid_checkout_and_receipt_never_grant_paid_access` |
| Invalid owner, payload, currency, amount or recurring facts rejected | Parameterized `test_malformed_or_mismatched_provider_receipt_is_rejected` and the checkout rule tests |
| Concurrent receipt replay grants once; renewal has distinct fresh allowance | `test_payment_receipt_replay_refund_and_cancel_preserve_independent_coverage`; `test_paid_period_gets_fresh_quota_without_changing_trial_ledger` |
| Renewal/cancellation/refund ordering preserves unrelated valid coverage | The native replay/refund/cancel scenario asserts one surviving second paid period, rejects out-of-order renewal and does not restore refunded coverage on replay |
| Cancellation targets the original charge and keeps paid-through rights | `test_cancel_adapter_targets_initial_telegram_charge`; the native cancellation scenario reads active paid access after confirmed cancellation |
| Overlapping purchase rejected; uncertain invoice not blindly resent | `test_scheduled_paid_coverage_blocks_checkout_and_another_order`; `test_worker_restart_fences_stale_invoice_claim_without_resending`; `test_unknown_invoice_send_is_not_reported_as_a_known_failure` |
| Precheckout persists before a bounded denial even under lock contention | `test_precheckout_lock_timeout_denies_and_keeps_persisted_update`; `test_webhook_persists_then_denies_checkout_when_order_lock_exceeds_budget` |
| Terms, explicit acceptance, support and unconfirmed order routes are durable and owner bound | The additional [support route proof](issue-20-support-local.md): one native scenario passed, zero failures/errors/skips, 9.557 seconds; RU owner remains unaccepted when EN owner accepts |
| Operator has a documented manual refund path with honest unknown outcome handling | [Payment operations runbook](../runbooks/payments_local.md), checked against `pin.entrypoints.billing_refund`; no financial command executed |

Support packet: `C:/Users/roman/AppData/Local/Temp/pin-issue20-support-local-20261015`. The Orchestrator directly checked `routes.junit.xml`, the fixture cleanup receipt and test SHA-256 `366b4e22a4014301f0c8e79a27d4d94d34c8a49d32b442b1c893cdaab94aa06c`, and independently passed Ruff and focused mypy. The fixture was absent after cleanup, no connections remained before its drop, protected databases were present, and the nonmanifest database set was unchanged. Earlier harness failures remain in the packet. This additional targeted case is not a new full-suite run.

## Acceptance boundary

The Orchestrator accepts the implemented #20 outcome for Windows local development under the user's standing rule-based acceptance delegation and amended verification scope. GitHub publication/closure follows the committed evidence. Only trusted synthetic transport facts and actual local database/application transitions establish the payment behavior here; no actual receipt, cancellation or refund was tested with Telegram. The owner removed that test requirement rather than accepting a fabricated provider success.

Kazakhstan entity form, tax regime and statutory financial retention remain unresolved prelaunch requirements tracked by #23/#28. No numeric financial retention period, public launch, paid service, deployment or overall MVP acceptance follows from this local acceptance. The separate #26 composed integration gate remains required.
