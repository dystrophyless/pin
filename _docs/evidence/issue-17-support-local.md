# Issue #17 planning support progress

Issue #17 remains open and in progress. Product requirements remain SPEC-001 §11 and the published task contract. This records implementation progress, not final acceptance.

Checkpoint: `542ea03` adds typed Russian/English advice, bounded ordered steps, durable advice history for explicit text selection, and ordinary clarification/preview/confirmation. Steps become ordinary tasks only through the existing confirmed creation flow; no priority/subtask persistence was added. The model receives only owned bounded Planning snapshots and is instructed to treat them as shared plans rather than a verified free calendar.

Accepted Advice must render within the existing 4,000-character Reply contract. Total rendered output and whitespace-only fields are validated before acceptance; numbering and all accepted steps are preserved. Invalid provider output follows the existing model failure/refund path.

Direct Orchestrator checks on 2026-10-14:

- Focused model/capture/support/advice-bound tests: **63 passed**, zero failures/skips.
- Guarded PostgreSQL runtime flow: **1 passed**, zero skips, in 7.83 seconds, using fresh owned `pin_issue17_root_bounds_20261014` on the assigned PostgreSQL 18.6 cluster. It covers owned advice without plan writes, prior-advice selection, missing-date clarification, editable batch preview, opaque confirmation, exact saved tasks, conflicting time, and exhausted/expired access without further model submission.
- Scoped Ruff and no-incremental mypy (seven files): PASS; scoped `git diff --check`: PASS.

The runtime check used the working tree, including in-progress outer Accountability composition. It is not a frozen-source whole-project qualification. The source/helper integration, all regression gates, exact revision/environment evidence, and task acceptance audit remain pending in the separate 1617a packet. No live provider or Telegram calls, Docker, deployment, or release qualification were performed.

The approved text request → preview → confirmation interaction is retained. The optional preference for an additional selection button has not changed this contract.
