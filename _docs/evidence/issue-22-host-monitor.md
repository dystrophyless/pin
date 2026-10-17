# Issue 22 hosted monitor evidence

Date: 2026-10-17.

## Observed result

The host has a root-owned systemd health service and an enabled timer that runs every five minutes. The first check reported PostgreSQL ready, all four required worker roles running, and all seven Compose containers running, with no reported errors or pending work.

A bounded failure probe stopped each of the four long-running lanes in turn: gateway, application worker, delivery worker, and speech worker. For each stop, the check reported the affected service as not running and exited unsuccessfully. Each lane was restored; after its heartbeat returned, a new health check succeeded. The database remained ready throughout.

After an unattended kernel security update required a reboot, the host came back on the new kernel. All seven containers started automatically, PostgreSQL became healthy, the monitor timer remained active, and a manual health check passed. The persistent bootstrap marker remained present and no further reboot was pending.

The monitor uses the existing redacted operations snapshot alongside host resource and container status. Local deployment-helper tests passed (22 tests), with focused lint and type checks passing. Existing local operations tests separately cover dead/expired work and worker-state diagnostics; provider and delivery unknown outcomes; redacted seeded speech and billing projections, including an overdue reserved usage hold and unknown invoice/refund; and injected low-disk/resource-measurement failures. These are selected synthetic cases; they do not exercise queue saturation, a payment-versus-usage mismatch, or a failed purge.

## Operator path and limits

An operator checks the systemd service result and journal, then follows the existing operations runbook. The monitor does not page externally. The hosted probes establish detection and recovery for the four long-running lanes and recovery after this reboot. Queue saturation, payment mismatch, failed purge, and restore/replay remain unverified across the current evidence. No full Issue 22 acceptance is claimed.
