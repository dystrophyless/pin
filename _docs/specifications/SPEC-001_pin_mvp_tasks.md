# Pin MVP — draft implementation task package

Status: **published issue index**. Published: 2026-10-13 (Asia/Qyzylorda).
Source: [SPEC-001 v0.2](SPEC-001_pin_mvp.md), including adopted section 11; [brainstorm](../PROJECT_BRAINSTORM.md) remains the requirements source. Technical reference: [DDD architecture](../ARCHITECTURE.md), subordinate to newer adopted product policies.

This is a concise index of 28 published GitHub Issues. GitHub owns task bodies, dependencies, blockers and workflow status. Semantic aliases are navigation names; actual issue numbers and links below are authoritative. Published local drafts are issue-link stubs. Owners and priorities remain unset. No implementation or acceptance is authorized by publication.

## Package contract and readiness

All 28 issues retain their reviewed scope and proposed verification. Live Workflow status and matching labels belong to GitHub; this index does not mirror task status. Section 11 resolves O01–O17 at the stated policy level. The [current package review](SPEC-001_pin_mvp_package_review.md) records the package review findings at publication. Later approved owner decisions in SPEC-001 section 11 supersede historical open-decision wording; any still-unresolved affected behavior must not be marked ready or implemented by guessing. Unaffected foundations can proceed when execution is authorized. Foundation tasks still verify actual dependency/provider compatibility; qualification and release gates still require runtime evidence and external credentials/provisioning. Failure of a gate is not permission to silently reduce confirmed scope.

At package publication, only documents, `.github/` templates and project skills existed. Subsequent authorized execution has established local runtime, persistence, durable work, presentation, ingress, access and model foundations; live issues link their actual source revisions, commands and acceptance evidence. Remaining proposed surfaces belong to the context/layer layout in the architecture and must be checked against the current checkout before implementation. Each producer owns its migration and domain logic; consumers use published contracts. `persistence` supplies UoW/migration conventions, not all future context tables. `work` owns infrastructure jobs/outbox, not business reminder policy. `delivery` owns the external-send boundary, not completion or billing truth.

Common delivery condition: a local reproducible result with meaningful automated evidence, reviewed diff, and limitations; hosted proof only where specified. Record revision, date, environment, command/scenario, actual output/artifact, and failure paths. Verification scenarios in published task contracts are requirements, not proof of execution. Accepted local issues record their actual commands, fixtures, observed results and limitations; remaining tasks must supply equivalent evidence using the established runtime commands. Use isolated accounts, an injected clock and disposable PostgreSQL; never advance a production clock. Compare persisted records and external attempt outcomes, not only handler responses. No task may claim product acceptance from mocks alone. Live models/Telegram, paid AWS experiments, provisioning, public deployment, and GitHub publication require authorization within the later execution assignment. Gate tasks report findings/verdicts; they do not quietly implement missing features.

## Proposed execution order

This is one valid topological order, not issue-number order:

1. `runtime`, `persistence`, `work`, `formatting`, `ingress`, `access`.
2. `profile`, `plans`, `agenda`, `recurrence`, `intents`, `delivery` — establish deterministic scheduling before AI.
3. `model`, `capture`, `mutations`, `accountability`, `support`, `voice`.
4. `trial`, `payments`, `landing`, `operations`, `retention`, `deployment`.
5. `core-gate`, `paid-gate`, `qualification`, `release-gate`.

This remains valid after grooming: `capture` now consumes recurrence/schedule previews, `model` consumes the persisted conversation-ordering contract, and deletion explicitly consumes dispatch/access fencing. The decision gates in the review are prerequisites for affected behavior, even though they are not task aliases.

Some independent contracts may be developed concurrently after prerequisites; overlapping schema, preview routing, deployment composition, and delivery consumers should be integrated sequentially. This order does not request subagents. Each issue's `Depends on` is authoritative and its explicit `Blocks` is the exact reverse edge set; downstream tasks transitively inherit prerequisite contracts. Their reverse dependency set defines what each task blocks.

## Task index

Full task contracts are in the linked GitHub Issues; this index contains no duplicate task bodies or workflow-status table.

| Local alias / task title | Intended outcome | GitHub Issue |
|---|---|---|
| `runtime` — Boot the DDD application with validated configuration | the agreed runtime starts in each process role from a locked, reproducible environment and rejects unsafe/missing configuration. | [#1](https://github.com/dystrophyless/pin/issues/1) |
| `persistence` — Prove transactional persistence and migration lifecycle | context repositories can atomically commit a mutation/receipt/event or roll back all of them. | [#2](https://github.com/dystrophyless/pin/issues/2) |
| `work` — Recover durable jobs and integration events after crashes | committed work survives restart and stale workers cannot finalize reclaimed jobs. | [#3](https://github.com/dystrophyless/pin/issues/3) |
| `formatting` — Render safe Russian/English Telegram results | structured results become usable localized messages and owner-bound controls without corrupt formatting. | [#4](https://github.com/dystrophyless/pin/issues/4) |
| `ingress` — Persist authenticated Telegram updates before acknowledging them | private-chat updates reliably enter durable processing without duplicate work or cross-user identity claims. | [#5](https://github.com/dystrophyless/pin/issues/5) |
| `access` — Enforce entitlement and usage reservations independently | every operation receives an authoritative access decision, with concurrent usage unable to overspend the chosen allowance. | [#6](https://github.com/dystrophyless/pin/issues/6) |
| `profile` — Complete onboarding and retain personal preferences | a returning user recovers explicitly confirmed RU/EN language, home zone, reminders, quiet hours, and review time; onboarding activates one trial. | [#7](https://github.com/dystrophyless/pin/issues/7) |
| `plans` — Persist validated one-off and untimed commands without duplicates | a confirmed typed command creates exactly one owned plan and preserves its intended calendar meaning. | [#8](https://github.com/dystrophyless/pin/issues/8) |
| `agenda` — Show persisted today/week plans and clear empty states | `/today` and `/week` display the user's saved local calendar accurately, including untimed and overdue items. | [#9](https://github.com/dystrophyless/pin/issues/9) |
| `recurrence` — Generate stable recurring occurrences and calendar exceptions | daily/weekly/monthly series generate independent, uniquely identifiable occurrences under the adopted calendar rules. | [#10](https://github.com/dystrophyless/pin/issues/10) |
| `intents` — Produce and invalidate useful reminder schedules | current one-off/recurring schedules produce the exact durable reminder intentions displayed in a preview/confirmation. | [#11](https://github.com/dystrophyless/pin/issues/11) |
| `delivery` — Send eligible reminders with honest failure outcomes | due reminders reach Telegram only under current plan/access/quiet eligibility, with visible confirmed, failed, and unknown attempts. | [#12](https://github.com/dystrophyless/pin/issues/12) |
| `model` — Normalize bounded Qwen proposals and actual usage | the selected Qwen route returns typed, validated proposals/replies or explicit failure, without direct domain mutations. | [#13](https://github.com/dystrophyless/pin/issues/13) |
| `capture` — Preview, clarify, and confirm text task creation | RU/EN text produces a fully clarified editable preview, then exactly one confirmed plan or atomic batch. | [#14](https://github.com/dystrophyless/pin/issues/14) |
| `mutations` — Confirm changes and enforce their delivery-consumer effects | previewed Done/Cancel/Reschedule and zone/series changes update the intended records without stale future notifications. | [#15](https://github.com/dystrophyless/pin/issues/15) |
| `accountability` — Run completion checks and one extra nudge | appointment/deadline/today's untimed review cycles ask about completion and issue at most one additional nudge. | [#16](https://github.com/dystrophyless/pin/issues/16) |
| `support` — Offer practical planning help without unsolicited writes | the assistant suggests steps/priorities/rescheduling from shared plans; selected steps become ordinary tasks only on request and confirmed preview. | [#17](https://github.com/dystrophyless/pin/issues/17) |
| `voice` — Queue local speech recognition into the confirmed-plan flow | valid Russian/English voice messages use the same clarification/preview/confirmation contract as text. | [#18](https://github.com/dystrophyless/pin/issues/18) |
| `trial` — Pause expired access and resume useful work without floods | trial/paid boundary transitions notify accurately, stop ineligible work, and preserve saved plans and allowable actions. | [#19](https://github.com/dystrophyless/pin/issues/19) |
| `payments` — Grant one valid subscription period per validated Stars receipt | a 250-Star recurring 30-day purchase/renewal grants the correct paid period once, with cancellation/support/refund handling. | [#20](https://github.com/dystrophyless/pin/issues/20) |
| `landing` — Refer Russian/English visitors into the correct bot | an accessible responsive Pin landing explains the actual product/offer and opens the intended Telegram bot. | [#21](https://github.com/dystrophyless/pin/issues/21) |
| `operations` — Detect stalled work and diagnose failures without private content | an operator can detect and trace reminder, model, speech, access and payment failures from redacted operational evidence. | [#22](https://github.com/dystrophyless/pin/issues/22) |
| `retention` — Delete operational data while preserving required financial evidence | confirmed account deletion and scheduled retention cleanup obey the selected policy without later restoring deleted personal data. | [#23](https://github.com/dystrophyless/pin/issues/23) |
| `deployment` — Recover a private staging installation from a tested backup | the composed staging processes start securely and recover persisted state within the adopted recovery targets. | [#24](https://github.com/dystrophyless/pin/issues/24) |
| `core-gate` — Prove the integrated planning-to-accountability lifecycle | one integrated evidence report proves the complete private RU/EN text/voice planner across the real delivery-consumer boundaries. | [#25](https://github.com/dystrophyless/pin/issues/25) |
| `paid-gate` — Prove paid access, quotas, and landing agree end to end | acquisition/payment/expiry/renewal/allowance flows preserve the correct rights and reminder behavior. | [#26](https://github.com/dystrophyless/pin/issues/26) |
| `qualification` — Qualify actual model, host and operating economics | measured evidence determines whether selected hardware/model route meets the agreed workload, accuracy and affordability requirements. | [#27](https://github.com/dystrophyless/pin/issues/27) |
| `release-gate` — Decide overall MVP acceptance from complete evidence | a release-readiness report accounts for the entire accepted MVP and every limitation, without confusing deployment authorization with acceptance. | [#28](https://github.com/dystrophyless/pin/issues/28) |

## Command ownership

| Approved command | Owning task / integrated consumer |
|---|---|
| `/start`, `/settings` | profile; mutations handles existing-plan zone effects |
| `/today`, `/week` | agenda |
| `/subscription` | trial with access/payments |
| `/help` | formatting localized deterministic handler; ingress routing |
| `/support` | formatting handler; operations provides configured operator contact/runbook |
| `/paysupport`, `/terms` | payments; landing links to the same terms/support |
| `/privacy`, `/delete_account` | retention; formatting/ingress adapters |

All routes remain deterministic, owner/private-chat bound where applicable, localized and available under the approved operation-right policy. Core/paid/release gates verify command completeness rather than inventing another independent product increment.

## Acceptance coverage

| Spec criterion | Owning implementation outcome(s) | Final proof |
|---|---|---|
| AC01 | profile | core-gate |
| AC02 | plans, capture | core-gate |
| AC03 | voice, capture | core-gate, qualification |
| AC04 | profile, plans, capture | core-gate |
| AC05 | intents, delivery | core-gate, qualification |
| AC06 | recurrence | core-gate |
| AC07 | accountability | core-gate |
| AC08 | accountability, mutations | core-gate |
| AC09 | agenda | core-gate |
| AC10 | formatting and its feature consumers | core-gate, paid-gate |
| AC11 | recurrence, mutations, profile | core-gate |
| AC12 | mutations, intents, delivery, accountability | core-gate |
| AC13 | support | core-gate |
| AC14 | access, trial, delivery | paid-gate |
| AC15 | landing | paid-gate |
| AC16 | model, voice, deployment | qualification |
| AC17 | work, ingress, delivery, operations | core-gate, release-gate |
| AC18 | ingress, access, plans, mutations, deployment | core-gate, release-gate |
| AC19 | access, payments, trial | paid-gate |
| AC20 | retention, operations, deployment | release-gate |

## Review provenance and current boundary

[Prior individual-task grooming](SPEC-001_pin_mvp_task_review.md) remains unchanged as historical evidence for the previously aggregated 28 bodies and their hashes. Its links to this package still resolve to this index; its historical task hashes describe the previous aggregate, not the extracted drafts. Its stronger recovery ambiguity is narrowed/refuted where §11 already answers it; no approved policy was rewritten.

[Current package review](SPEC-001_pin_mvp_package_review.md) records independent findings, complete requirement/contract/ownership/gate coverage, exact frozen/final hashes, two bounded rounds and actual document checks. It is the current package-readiness record, not a task tracker or new product specification. Product decisions must be recorded by the owner in an approved amendment before affected implementation; runtime/environment prerequisites require evidence before affected gates can pass.

Issue publication and dependency linking were authorized on 2026-10-13. All 28 issue bodies contain scope, exclusions, AC, verification, delivery conditions, source path/hash references and explicit readiness/resume conditions. The 95 dependency edges use real issue references with exact reverse Blocks. Workflow status is maintained in GitHub only. Reviewed source documents remain local and unpushed; publication of issues does not publish repository files. No product implementation, verification, acceptance or public deployment is established here.
