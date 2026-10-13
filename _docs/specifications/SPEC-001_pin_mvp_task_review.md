# SPEC-001 MVP task grooming review

Date: 2026-10-13 (Asia/Qyzylorda). Method: independent review with project-local [spec-task-review](../../.agents/skills/spec-task-review/SKILL.md) and [Ponytail](../../.agents/skills/ponytail/SKILL.md), full mode. Task-document corrections were authorized; product implementation and external publication were not.

## Verdict and exact boundary

**All 28 local drafts reviewed and groomed; conditional draft, not unconditional implementation readiness.** TD-01–TD-07 task-document gaps are fixed within the unchanged approved baseline. TR-01–TR-03 need narrow product clarification for affected behavior. TR-04 is an already-required qualification approval/evidence prerequisite, not a reopened feature decision. Unaffected foundations can proceed in a later authorized assignment; affected portions remain decision-gated. No runtime bug, passing product criterion, issue status or owner acceptance is inferred.

Repository `D:\Program\python\pin`, branch `main`, HEAD `1ec4adfb1104f58ddb1a0553f890e7b1aa00f166`. Reviewed working-tree bytes, SHA-256:

| Source / boundary | SHA-256 |
|---|---|
| [Approved SPEC-001 v0.2](SPEC-001_pin_mvp.md), all sections, unchanged | `4C32543493E0EF33070ED43ABA643527AC663503467F1427F6EFEA10D19C61AC` |
| [Task package](SPEC-001_pin_mvp_tasks.md), pre-review 28 drafts | `444472554A7DAB01BE74ED50C9C4F2029AEE9AB55A4360D278149C0BC3E5EBA3` |
| Task package, groomed final reviewed bytes | `01CC3DE98541284E91B4E83463A8F0BEDC785EC0DA0569E94A9EFAFE00DE9A47` |
| [Brainstorm](../PROJECT_BRAINSTORM.md), original requirements/historical research | `289D8D76DA7BBC377FF4E199F9F6FFE9CD08E2316264AD502391632210577A9F` |
| [Architecture](../ARCHITECTURE.md), subordinate technical proposal | `BD279F5E6C6CE1CF0F59D55C225E5FF84BA6DCCB2B44A700C78331C02678AC13` |
| [AGENTS.md](../../AGENTS.md) | `436526FEEE10FA3248233EFBF5F36B500C32DDD55C12E196969D4F2D9AE31A9C` |
| Ponytail skill | `FC12A0B9588A7FA3543D31DDC41541F735A5626105781643873110F2DA1BD52A` |
| spec-task-review skill | `D052ADC81875DAC99699A55680EAB7F8215EBE6355EEDDC0E02A634FB48E0EC9` |

Also read [HANDOFF](../HANDOFF.md), [SDD workflow](../sdd/README.md) and [decomposition guide](../sdd/guides/task-decomposition-guide.md). Existing unrelated edits in `.agents/skills/task-package-review-loop/SKILL.md`, README, HANDOFF and specifications index were preserved. Scope of edits is this report and the task package only. Specification/tasks are untracked working-tree inputs; HEAD alone is not their revision. No live issue discussion was selected or reviewed; aliases are not GitHub issue numbers. This review does not certify current GitHub state.

`git status --short`, `git rev-parse HEAD`, `rg --files` and filesystem inventory establish documentation, skills/templates/configuration and no product code, dependency manifest, migrations or application suite. There are no implemented entry points/consumers to trace with semantic search. Proposed fixture plans must be anchored to actual code/commands later. No dependencies installed, product code written, PostgreSQL/model/Telegram/AWS/payment tests run, commits/pushes/issues/deployment performed.

## Severity-ordered findings

IDs are stable after correction. A confirmed document gap is not a confirmed runtime defect.

| ID | Severity / status / class | Impacted AC and tasks | Disposition |
|---|---|---|---|
| TD-01 | HIGH / confirmed / preview and decomposition gap | AC01/02/04/06/11/12; profile/plans/recurrence/intents/capture/mutations | Fixed task documents |
| TD-02 | HIGH / confirmed / consumer reliability proof gap | AC05/07/08/12/17/18; work/delivery/mutations/accountability/gates | Fixed task documents; runtime unproved |
| TD-03 | HIGH / confirmed / usage/payment seam proof gap | AC03/14/16/19; access/model/capture/voice/trial/payments/paid-gate | Fixed task documents |
| TD-04 | HIGH / confirmed / deletion/restore proof gap | AC17/18/20; retention/work/access/voice/delivery/deployment | Fixed task documents; TR-03 remains |
| TR-01 | HIGH / unconfirmable / recovery behavior ambiguity | AC05/07/08/14/17; delivery/trial/accountability/core-gate/paid-gate | Product clarification required for affected recovery |
| TR-02 | HIGH / unconfirmable / future recurrence semantics | AC06/11/12; recurrence/mutations/capture/core-gate | Product clarification for future edit projection |
| TR-03 | HIGH / unconfirmable / deletion-return identity/access | AC01/14/18/19/20; retention/profile/access/payments/deployment | Product clarification for return/linkage policy |
| TD-05 | MEDIUM / confirmed / routes/calendar verification gap | AC01/04/09/10/13/15; formatting/ingress/profile/agenda/support/landing/gates | Fixed task documents |
| TD-06 | MEDIUM / confirmed / readiness/evidence wording gap | AC01–AC20; all drafts/gates | Fixed task documents; original review preserved as historical |
| TD-07 | MEDIUM / confirmed / foundation/operations ownership gap | AC16–AC20, C-DDD; runtime/persistence/work/operations/deployment | Fixed task documents |
| TR-04 | MEDIUM / unconfirmable / qualification prerequisite | AC16/A01–A04; qualification/release-gate | Owner-approved rubric/workload and actual evidence still required |

### Fixed document findings — proof and applied action

**TD-01.** Original capture depended on plans/agenda/model but not recurrence/intents, although F05/AC06 and section 11 require supported recurring creation and a fully resolved schedule preview. Profile preview precedes the later capture Draft schema, which depends on profile. Added capture recurrence/schedule-projection dependencies, deterministic profile-owned confirmation state, full recurring preview and stale correction/preference/access fixtures. Typed commands still require confirmation/current rights; atomic batches commit together. Existing-plan zone changes remain mutations-owned. TR-02 is not silently resolved.

**TD-02.** K07/K08, §6 transactions and §11 require last-moment authority, leases, confirmed-send timers and honest races. Original broad plans did not enumerate all stale business/usage/transcript effects or crash/send/fallback/cycle interleavings. Added explicit stale-token restrictions, pause/reclaim tests before check/send and after send/before commit, current plan/access/owner checks, and API-confirmed versus read/arrival evidence. Unknown sends cannot auto-retry or trigger format fallback. One business nudge intent can have confirmed-failure retries; no timer starts from failed/unknown initial send. Runtime proof requires actual consumer/DB/attempt evidence; a successful cancellation response alone is insufficient. TR-01 remains.

**TD-03.** K09/K10 and §11 define user-turn/decoded-second units, period reservations, refunds, receipt-only grants, fresh trial upgrades and distinct provider costs. Added dual-unit contention/crash reconciliation, immutable request/period identity, deterministic controls free, internal call/repair cost accounting, refund/renewal/receipt reordering and separate valid-period coverage fixtures. Successful preview processing later abandoned by a user is not a processing failure refund. Current protected rights are checked independently at execution; ledger recovery algorithm is an executor-owned technical choice preserving invariants. No assumed Telegram sandbox or successful live payment claim.

**TD-04.** AC20/§11 require operational deletion, audio/text cleanup, seven-day backups and deletion replay. Original retention dependencies lacked mutations/delivery/access and did not demonstrate active producers or restore startup ordering. Added those dependencies, cancellation of reservations/drafts/jobs/intents, stale inference/download/outbox publication suppression, immediate success/failure cleanup and 24-hour crash-file bound. Restore fences ingress/workers before tombstone/usage/financial/unknown-send reconciliation. Already-started sends and provider/Telegram copies have honest limitations. TR-03 remains.

**TD-05.** §11 lists eleven deterministic commands and exact calendar/voice/formatting behavior. Added full command ownership map to existing tasks, onboarding step/restart/settings fixtures, local interval/Monday/DST/old-untimed/status/owner agenda cases, decoded 180-second/10-MB boundaries and misleading metadata. Preview and persisted schedules use the same projection; explicit overrides/past defaults/quiet deferral are concrete. Actual RU/EN Telegram renders and staging bot navigation remain required separate evidence.

**TD-06.** Original package asserted no policy prerequisites while only structural authoring evidence existed. Added concrete positive/negative fixture expectations, per-task owner/consumer boundaries, proposed-versus-executed evidence language and decision gates. Original self-review is historical, superseded for narrower semantics. Actual commands must be established by runtime and recorded later. Final gates require same-revision criterion evidence and distinguish local/staging/qualified/accepted/rollout authority.

**TD-07.** Spec §§4–6 and decomposition guide require owned foundation outcomes, feature-owned migrations and consumers. Added bounded startup/lock/Linux/speech-loading proof, no external calls inside UoW, rollback/prior-schema/disposable-DB fixtures, actionable redacted operational signals/runbooks and encrypted off-host key/expiry/recovery proof. No speculative scaffolds/new platform or implementation acceptance was added.

### TR-01 — Missed reminders/checks and recovery summary selection

Source: §11 user choice sends useful coalesced reminders if event is ahead, skips expired advance reminders, offers one unresolved-past summary; routine rules prohibit restarting exhausted cycles. These settle no flood/no auto-reschedule/one-nudge invariants but not exact recovery selection.

Concrete decision: outage ends 13:30 for a 14:00 appointment with unsent 12:00/13:00 reminders. Omit both as expired advance reminders, or send one coalesced useful reminder because the event is ahead? If outage ends after appointment end before any initial check-in was delivered, send a late initial check-in, include only in summary, or both? Also define durable summary deduplication for repeated reconciliation of the same recovery versus genuinely later episodes.

Action: owner records a small before/after fixture table, without invented grace windows or renewed nudge campaigns. Affects recovery portions only. delivery/trial/accountability then implement it; core/paid gates use injected-clock staging/DB/attempt evidence. Ordinary timely dispatch and known one-nudge timer remain settled.

### TR-02 — Future series split, interval anchor and count/end

Source: §11 adopts this occurrence or this-and-future, preserves history, says stop limits operate on generated occurrences and completing one never completes later repeats. Generic Done scope must therefore remain occurrence-only unless the owner explicitly changes the baseline; this part is already determined.

Concrete decision: after four instances of a count-10 series, moving instance five with this-and-future can preserve six remaining instances or apply a new count to the future series. An N-interval weekday/time change can retain original interval anchor or reanchor from the selected occurrence. Future dates/counts differ. The source does not select effective split/count/anchor rules.

Action: owner specifies before/after fixtures for supported future edit/reschedule/cancellation and scope menu. Preserve stable exceptions/occurrence identities/history and show the resulting dates. Preview confirmation alone does not justify silently redefining repeat meaning. Unchanged generation, one-off changes and single-occurrence Done are not blocked.

### TR-03 — Delete account, then the same identity returns

Source: §11 requires user-linked operational data removal, no trial reset, backup/tombstone handling and separately minimized financial receipts. It does not define post-deletion minimal identity/access linkage, lifetime or return rights.

Concrete tension: after deletion the same Telegram user sends `/start`. A fresh trial violates no-reset; automatic restoration of deleted operational profile violates deletion. A still-valid separate paid receipt may survive, but its permitted linkage and return behavior are unspecified. Restore-prevention tombstones do not themselves authorize indefinite anti-reset identity retention.

Action: owner defines return rights and permitted minimal anti-reset/valid-paid-coverage linkage, purpose/lifetime/privacy disclosure. A restricted marker separate from operational profile or an explicit return/reconciliation procedure are concrete alternatives to assess; neither is implicitly approved. Do not retain indefinite identifiers or grant a new trial by guessing. Then verify delete -> return -> receipt/replay/restore. Existing cleanup/fencing/seven-day restore rules remain required independently.

### TR-04 — Quality/traffic qualification rubric

Source: AC16/§11 already require approved traffic profile, actual host/provider measurement and owner suitability acceptance. Adopted latency targets exist; representative language/noise corpus and tolerated scheduling/transcription errors do not.

Action: before suitability PASS, owner approves corpus/workload, burst arrivals, sample size and error/triage rubric (especially unsafe date/time guesses), then bounded actual-host/provider evaluation records raw errors, p95, queue wait separately and sustainable costs after credits. This is a planned acceptance/evidence prerequisite, not a new product policy. Failed targets require reviewed remediation, not silent allowance/model changes.

## Coverage: all 28 independently reviewed

Every row is reviewed/amended task-document evidence, not implemented or verified runtime behavior. References use unchanged v0.2.

| Task | Spec contract / AC | Disposition and residual |
|---|---|---|
| runtime | C-DDD/R15/§11 | TD-06/07: locked role/config/import/speech boundaries |
| persistence | §6/AC17/18 | TD-01/07: atomic receipt/event, conflicts and migrations |
| work | K01/K07/AC17 | TD-02/03/04/07: renewal/reclaim/stale effects/ordering |
| formatting | O10/AC10/18 | TD-02/05: safe tokens/splits/confirmed-format fallback |
| ingress | K01/O17/AC17/18 | TD-05/07: bot identity/private routing/durable acknowledgment |
| access | K09/AC14/19 | TD-03/04: exact rights/unit/period/contention; TR-03 linkage |
| profile | F01/AC01/11 | TD-01/05: ordered own confirmation, restart/settings; TR-03 return |
| plans | K03/AC02/04/11 | TD-01/03/05: typed confirmation/access/kind/calendar/UoW |
| agenda | K04/AC09 | TD-05: intervals/DST/Monday/status/old untimed/expired reads |
| recurrence | O04/AC06/11 | TD-01/05: repeat/clamp/DST/identity/repair; TR-02 future edits |
| intents | O01/O03/AC04/05/12 | TD-01/02/05: shared preview/persistence/provenance/quiet/version |
| delivery | K07/AC05/12/14/17 | TD-02/04: fenced authority/faults/send evidence; TR-01 recovery |
| model | K02/AC16/18 | TD-02/03: owned context/call budget/tools/cost; work dependency |
| capture | K02/K03/K11/AC02/04/06 | TD-01/03/05: recurring preview/corrections/restart/batch |
| mutations | K05/K08/AC11/12 | TD-01/02: deterministic preview/zone/history/races; TR-02 |
| accountability | K08/AC07/08/12 | TD-02/03/05: confirmed-send timer/one nudge/daily scope; TR-01 |
| support | O09/R16/AC13 | TD-01/05: advice no writes/requested ordinary steps |
| voice | K06/AC03/16/20 | TD-02/03/04/05: decoded bounds/continuation/usage/files/order |
| trial | O11/O13/AC14 | TD-02/03/05: exact expiry/notices/cycles/subscription; TR-01 |
| payments | K10/AC19 | TD-03/05: terms/overlap/upgrade/receipt/refund/reorder; TR-03 |
| landing | O14/F10/AC15 | TD-05/06: bilingual truth/renewal/links/real CTA |
| operations | O15/O16/AC17/20 | TD-04/06/07: actionable redacted failure/ledger/purge signal |
| retention | O16/AC18/20 | TD-04/05: active-work/cleanup/restore; TR-03 return marker |
| deployment | O15/O16/AC18/20 | TD-04/07: isolated restore-before-resume/keys/expiry/RPO/RTO |
| core-gate | AC01–AC13/core AC17/18 | TD-01/02/05/06: real per-scenario evidence; TR-01/02 |
| paid-gate | AC14/15/19 | TD-03/04/05/06: financial/period/quota/delivery; TR-01/03 |
| qualification | AC16/A01–A04 | TD-06/07: actual errors/traffic/host/cost; TR-04 |
| release-gate | AC01–AC20 | TD-06/07: same-revision manifest/decisions/acceptance |

## Final dependencies and evidence buckets

Added edges: model depends on work; capture on recurrence and intents; retention on mutations, delivery and access. No new task/issue IDs or assignees. Reverse Blocks sets derive from Depends on. Valid final order:

1. runtime, persistence, work, formatting, ingress, access.
2. profile, plans, agenda, recurrence, intents, delivery.
3. model, capture, mutations, accountability, support, voice.
4. trial, payments, landing, operations, retention, deployment.
5. core-gate, paid-gate, qualification, release-gate.

Overlapping schema/preview/deletion/dispatch surfaces require sequential integration even if individual dependency edges allow independent work. Dependencies establish prerequisites, not implemented/accepted status. Decision gates are not task aliases.

- Confirmed: TD-01–TD-07 document gaps/corrections, source bytes, task count and local-only boundary.
- Refuted: original stronger claim that no narrower product clarification remains (TD-06); document structure alone proves no runtime acceptance.
- Partial: all AC have named owners/proof and substantive fixtures; this is traceability, not future implementation proof. Architecture is a concrete proposal, not installed compatibility.
- Unconfirmable: TR-01–TR-03 product intent; TR-04 qualification rubric/actual measurements; all future consumer reliability/security/payment/model/host/jurisdiction/release evidence.

No human answer is needed to finish this review. Before affected implementation, record owner answers for TR-01–TR-03 in an approved decision/spec amendment and update affected fixtures. Before qualification, approve TR-04 rubric/workload. Foundation implementation establishes actual commands and subsequent code anchors. Final gates require compatible revision, real evidence and separate owner acceptance; public rollout requires later authorization.

## Executed verification and limitations

Environment: Windows PowerShell, `C:\Program Files\Python313\python.exe`, local dirty worktree/hash boundary above, 2026-10-13. Executed PASS: 28 unique complete bodies and eight required fields; valid aliases; DFS acyclic graph and declared topological order; AC01–AC20 coverage; all 28 review dispositions and 11 finding IDs accounted; local Markdown links/fences; direct whitespace/conflict-marker checks; unchanged approved-spec hash; recursive inventory found no product source/manifests outside tooling metadata. `git diff --check` exited 0; it emitted only LF/CRLF conversion warnings for pre-existing unrelated tracked edits. No runtime result was claimed. Local outputs: document assertion tool chunk `6a7468` and Git check/status chunk `2608ea` in this review session; these are tool-output references, not durable external URLs.

Manual semantic check: read each original task against spec flows/data/K contracts/AC and adopted §11; inspect one owned outcome, producer/consumer dependencies, exclusions, positive/negative fixtures, safe environment and evidence limits. Findings and every disposition are above. This is document inspection, not proof of arbitrary future code behavior.

Commands executed: repository/status/file/hash inspections above; a PowerShell here-string piped to `python -` for the document assertions; `git diff --check`. Document assertions check all 28 aliases and eight body fields, valid dependency aliases, DFS no cycles/topological order, AC01–AC20 coverage table, local Markdown file targets/fences, preserved approved-spec hash, exact groomed-task hash, no trailing whitespace/conflict markers in both review-owned files, and direct recursive absence of product source/manifests. The full assertion script/result is in the current review tool output, not a fictitious application test command.

Structural checks establish only document shape/hash/link/dependency consistency; manual review establishes the scoped doc findings. Neither proves runtime readiness, real rendering, provider compatibility, jurisdictional financial retention, cost or owner acceptance. Live links/Telegram/payment/AWS/DB/voice/runtime checks remain not run and explicitly planned in tasks because no application exists and this assignment authorizes documentation grooming. `git diff --check` alone omits untracked drafts, so direct draft/report whitespace checks are also executed. No commit/push/issue/deployment/code or approved-spec edit was performed.
