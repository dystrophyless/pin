# Issue 22 operational evidence and remaining limits

Date: 2026-10-18. This record consolidates the local operational checks, hosted health observations, and the persisted deterministic support-route proof. It does not claim whole-issue acceptance.

## Local operational coverage

The frozen local qualification includes seven PostgreSQL operations cases in [`tests/test_operations_postgres.py`](../../tests/test_operations_postgres.py). Their scope includes bounded queue and worker diagnostics, synthetic delivery/provider unknown outcomes, stuck reservations, missing receipt linkage, failed purge visibility, recovery fencing, and read-only/redacted projections. The source for those cases has not changed since that qualification. The nine host-health unit cases in [`deploy/test_host_health.py`](../../deploy/test_host_health.py) are also unchanged. These are local tests; synthetic fault rows do not establish that an external provider or Telegram actually produced the represented outcome.

Earlier focused records document additional detail: [bounded queue and worker snapshots](issue-22-faults-local.md), [context fault projections](issue-22-context-faults-local.md), [producer-to-snapshot unknown outcomes](issue-22-produced-faults-local.md), and [low-disk measurement failure handling](issue-22-low-disk-local.md). The local operations [runbook](../operations-runbook.md) preserves the operator steps and redaction limits.

The expired-account command seam now has persisted consumer coverage in [`tests/test_payment_support_routes_postgres.py`](../../tests/test_payment_support_routes_postgres.py). The focused run passed **14 tests** across that PostgreSQL module and [`tests/test_telegram_presentation.py`](../../tests/test_telegram_presentation.py). For expired Russian and English owners, `/help`, `/support`, and `/paysupport` pass through persisted ingress, application processing, and the reply outbox. The checks verify localized replies, the approved `@pin_appbot` support destination, inactive access, and no paid period, usage reservation, or planning item. The worker receives no assistant, so the scenario makes no model call. Existing private/group ingress rejection coverage remains in [`tests/test_telegram_ingress_postgres.py`](../../tests/test_telegram_ingress_postgres.py); that module was not rerun for this change.

## Hosted health and recovery observations

The [hosted monitor record](issue-22-host-monitor.md) documents all four long-running roles stopped and detected individually, each role restored and checked healthy, and a subsequent health pass after the kernel reboot. The direct host probes show detection and return to healthy operation for those events. They are separate evidence from the injected synthetic fault cases above. The monitor writes redacted signals to the system journal; it does not page externally or contact an operator.

The later [hosted recovery record](hosted-recovery-20261018.md) documents a successful isolated restore and recovery-fence verification against the deployed archive format. It does not establish four-way reconciliation, authorize a live gate release or cutover, or prove reconciled-recovery RPO/RTO. That record also documents the later successful real host-health invocation after the restore rehearsal.

## Remaining limits

This evidence does not establish automatic provider reconciliation, actual payment/refund or Telegram-send outcomes, external paging, a maximum queue-capacity policy, or statutory financial-retention compliance. Operational projections identify work for investigation; they do not repair billing, resend uncertain external operations, or prove that backups contain all required owner data. The hosted recovery evidence still leaves four-way reconciliation and restart authorization as separate gates.

The support-route correction passed [CI and deployment](https://github.com/dystrophyless/pin/actions/runs/39125355300). Root then inspected the selected server release and ran its scheduled health service: `Result=success`, `ExecMainStatus=0`. This confirms deployment and the aggregate host check; it does not claim a fresh client-visible support exchange. Local tests, hosted health observations, and the isolated restore remain distinct evidence boundaries.
