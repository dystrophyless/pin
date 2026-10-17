# Issue 22 hosted monitor evidence

Date: 2026-10-17.

## Observed result

The host has a root-owned systemd health service and an enabled timer that runs every five minutes. The first check reported PostgreSQL ready, all four required worker roles running, and all seven Compose containers running, with no reported errors or pending work.

A bounded failure probe stopped each of the four long-running lanes in turn: gateway, application worker, delivery worker, and speech worker. For each stop, the check reported the affected service as not running and exited unsuccessfully. Each lane was restored; after its heartbeat returned, a new health check succeeded. The database remained ready throughout.

After an unattended kernel security update required a reboot, the host came back on the new kernel. All seven containers started automatically, PostgreSQL became healthy, the monitor timer remained active, and a manual health check passed. The persistent bootstrap marker remained present and no further reboot was pending.

The monitor uses the existing redacted operations snapshot alongside host resource and container status. Local deployment-helper tests passed (22 tests), with focused lint and type checks passing. Existing local operations tests separately cover dead/expired work and worker-state diagnostics; provider and delivery unknown outcomes; redacted seeded speech and billing projections, including an overdue reserved usage hold and unknown invoice/refund; and injected low-disk/resource-measurement failures. These are selected synthetic cases; they do not exercise queue saturation, a payment-versus-usage mismatch, or a failed purge.

## Operator path and limits

An operator checks the systemd service result and journal, then follows the existing operations runbook. The monitor does not page externally. The hosted probes establish detection and recovery for the four long-running lanes and recovery after this reboot. At this initial monitor checkpoint, payment mismatch, failed purge, and restore/replay were not verified; later focused results are recorded below and in the recovery evidence. No full Issue 22 acceptance is implied by the initial probes.

## CPU burst visibility — 2026-10-17

Root opened the existing Pin instance in the AWS Lightsail console and selected **Metrics → CPU overview**. Both CPU utilization and remaining CPU burst capacity were visibly available. In the displayed one-hour window (20:30–21:30, UTC+5), remaining burst capacity rose approximately from 49% to 52%; this is a visual graph reading, not an exported exact data point. The private screenshot is retained with the host evidence. Local CPU percentage and available-core measurements do not substitute for this AWS credit/capacity metric. This observation establishes an operator inspection route, not model throughput, concurrency capacity or an economic benchmark.

## Later focused diagnostics verification

Source `b0eafcb` adds bounded queue correlation IDs, platform-owned recovery-gate progress, and a Billing-owned active-period/order/reservation trace. It omits raw provider charge IDs, arbitrary request IDs and private payloads; a truncated receipt sample explicitly reports unavailable linkage rather than falsely declaring a missing receipt. The host summary exposes counts and actionable investigation signals, with no financial repair or cross-unit arithmetic.

The final focused PostgreSQL module passed **7 tests, zero failures/errors/skips** in 11.17 seconds. It includes actual confirmed deletion/tombstone creation followed by injected purge failure, bounded pending work, a synthetic missing payment linkage and a fenced recovery gate. Root checked the passing JUnit and exact fixture cleanup receipt. All nine host-health unit tests passed separately. The earlier trace run predates the final redaction/bounding fixes and is retained as historical evidence only. A bounded diagnostic sample is not a configured maximum queue capacity; no queue-capacity policy is invented.
