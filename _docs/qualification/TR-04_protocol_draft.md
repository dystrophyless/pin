# TR-04 measurement protocol — draft for review

**Status: DRAFT / PROPOSED.** This is a measurement worksheet and a small scenario candidate, not an approved corpus, a product-policy amendment, a completed qualification, or a suitability decision. It does not authorize provider spend, host provisioning, deployment, or external model/audio calls.

## Authority and boundaries

**Confirmed by the owner and recorded in SPEC-001 v0.10, §11:** the date/time interpretation target is no more than one error per 100 unambiguous requests before manual correction, equivalent to at least 99% correctness in the evaluated set. Apply the target independently to four confirmed groups: **RU text, RU voice, EN text and EN voice**. A combined language or channel average cannot mask a failing group. For voice, assess the first proposal against the original recording; transcript-only accuracy does not establish the voice result. An unnecessary date/time clarification on a clear request is a confirmed unsuccessful quality result; required clarification of genuinely ambiguous or incomplete information remains in force. The existing operational targets are qualification at 50 daily active users with a documented concurrent-arrival profile, p95 healthy dispatch-start at or below 10 seconds, and p95 processing of a 180-second voice input at or below 60 seconds at concurrency one, with queue delay reported separately.

**Open owner decisions:** sample size and representativeness; traffic arrivals and bursts; how error events are counted and graded; scoring of missing proposals, other failures, and unusable inputs; percentile method and cold/warm-run definitions. The 100-request denominator in the confirmed accuracy target does not approve a 100-case corpus or sample size. Do not report an accuracy or suitability PASS until the rubric and workload are approved and actual evidence exists.

The task review for [TR-04](../specifications/SPEC-001_pin_mvp_task_review.md#tr-04--qualitytraffic-qualification-rubric) calls for an owner-approved corpus/workload, burst arrivals, sample size, and error/triage rubric before suitability PASS, followed by bounded actual-host/provider measurements. This draft does not resolve those choices.

## Proposed measurement sequence

1. Freeze the approved scenario manifest and rubric before evaluation. Keep separate RU-text, RU-voice, EN-text and EN-voice result rows. Keep each raw request and attempt identifiable; do not discard retries, clarification turns, missing proposals, or failures from the raw record.
2. For each request, capture the input and the exact interpretation context before processing: language, input channel, application revision/runtime, selected model and route, speech model/route when applicable, reference instant in UTC, IANA home zone, and any explicitly requested event zone. Do not infer a home zone from language.
3. Save the first normalized proposal or clarification outcome before a person corrects or confirms it. Compare a clear case with its approved expected local date, local time, event zone, and corresponding UTC instant. Record every differing field and a plain-language error reason. Preserve the corrected/final value as a separate field; it must not overwrite the first proposal.
4. Record clarification, no-proposal, rejected-input, and processing-failure outcomes as their own observations. Keep them visible in language/channel-specific counts. An unnecessary temporal clarification of a clear request is unsuccessful by owner decision. Scoring of other no-proposal, rejected-input and processing-failure outcomes remains pending.
5. Preserve actual provider usage and cost facts by request where available. Represent absent or unresolved usage/cost as `unknown`/null, never as zero. The cost aggregation and acceptance rubric remain open. Do not repeat an unknown external call to try to obtain a receipt.
6. For a future approved workload run, record actual arrivals and stage timestamps. Report healthy dispatch-start separately from voice queue wait and speech processing. The proposed timings are `accepted_at_utc`, `worker_started_at_utc`, `speech_started_at_utc`, `speech_finished_at_utc`, and `result_ready_at_utc`; do not combine queue delay with speech processing. Use real 180-second voice inputs only when the required evaluation is separately authorized; this draft contains no audio.
7. Report raw counts and observations for each of the four language/channel groups before any aggregation. Apply the confirmed accuracy ceiling separately to each group only after the owner approves the error-counting and exclusion rules. Report latency against the already adopted targets only after the owner approves the workload, sample size, percentile method, and cold/warm treatment.

## Proposed raw observation fields

Capture one record per input attempt, retaining a stable opaque `request_id` and `scenario_id`:

| Field | Required raw fact |
|---|---|
| Provenance | Run ID, scenario ID, request/attempt ID, source revision and dirty-tree status, locked dependency/runtime versions, host/OS configuration, and timezone database version. |
| Route | Input language and channel; exact selected model/route/version and relevant inference settings; for voice, exact speech model/route/version and an approved real audio artifact reference/hash. Never store credentials in the record. |
| Temporal reference | Exact reference instant in UTC, IANA home zone, explicit event zone if supplied, and the original input. Preserve the original RU/EN text; for real voice keep the approved artifact reference and the actual transcript separately. |
| Expected and first result | Approved expected local date/time/zone and UTC instant when the case is clear; first proposal's normalized local date/time/zone/instant, or exact outcome class when there is no proposal or a clarification. Store this before any manual correction. |
| Difference and correction | Field-level differences, observed error reason, clarification/no-proposal/failure facts, whether a manual correction occurred, and the final confirmed value in separate fields. The severity rubric and treatment in the score remain pending. |
| Timing | UTC timestamps for accepted arrival, dispatch start, speech start/end where applicable, interpretation start/end where available, and result ready. Preserve source timestamps and derive queue wait, dispatch-start, and speech-processing durations separately. |
| Usage/cost | Actual usage receipt, token/decoded-second facts and billed cost if returned; explicit unknown/null status if absent or uncertain. Never coerce unknown to zero. |
| Exclusion/audit | Any proposed exclusion, reason, author, and approval reference. Do not silently remove a sample; exclusion policy and approval remain pending. |

The raw manifest should retain the exact context that makes each expected value reproducible. Protect user content and audio under the applicable approved handling rules; no retention duration or new data-collection policy is proposed here.

## Proposed first-pass comparison, with scoring still open

For a case labeled `clear`, compare the first proposal against the reference calendar values before manual correction and record exact field differences. For a case labeled `needs_clarification`, record the ambiguity and requested clarification; do not invent one expected instant. The draft comparison taxonomy is descriptive only: `date_mismatch`, `time_mismatch`, `event_zone_mismatch`, `instant_mismatch`, `clarification`, `no_proposal`, `processing_failure`, or `other`, with a free-text reason. Unnecessary temporal clarification of a clear request is already confirmed unsuccessful; the remaining taxonomy does not assign severity or decide how other no-proposal/failure outcomes enter the score.

For the confirmed ceiling, preserve both raw counts and the eventual numerator/denominator per language/channel group. The owner still needs to approve whether multiple mismatching fields in one request count as one error or several, how other no-proposal/failure outcomes are handled, and which cases may be excluded. Until those rules are approved, show the counts without a pass/fail label.

## Proposed workload record; no workload is selected here

The specification names 50 daily active users and requires a documented concurrent-arrival profile; it does not define concurrency, peak-hour distribution, bursts, or a run duration. Before any host/provider run, record the owner-approved arrival schedule and its source, RU/EN mix, text/voice inputs, target host shape, route versions, concurrency, queue policy, and run duration. Do not substitute a guessed traffic curve or a “50 DAU” label for those details.

Record the adopted thresholds verbatim: healthy dispatch-start p95 <= 10 seconds; 180-second voice input processing p95 <= 60 seconds at concurrency one; queue delay separately. Report the actual sample and percentile method. Cold/warm definitions are still open. Host capacity, actual provider behavior, sustainable economics, and suitability remain unmeasured/deferred under the current local-only authorization.

## Small candidate set

[`TR-04_scenarios_draft.jsonl`](TR-04_scenarios_draft.jsonl) contains 12 compact RU/EN examples, six per language. It is a **sampled draft candidate only**: eight clear references and four cases that should clarify under existing requirements. It is not a representative corpus, a statistically sufficient sample, or a measured result for any of the four required groups. All rows are text reference prompts; no voice/audio is fabricated or implied.

The candidate includes one-off DST-overlap cases because §11 explicitly requires asking the user to choose earlier/later for an ambiguous one-off local time. This small sample leaves other already-approved temporal patterns for a later corpus and claims no coverage of them.

Calendar calculations were checked with Python 3.12.12 `datetime`/`zoneinfo` and installed `tzdata` 2026.5 for `Asia/Qyzylorda`, `Europe/Berlin`, `America/New_York`, and `UTC`. This validates the candidate's arithmetic only; it is not interpreter/model evidence.

## Open before qualification

- Approve representative corpus size and sampling for all four confirmed language/channel groups.
- Approve ambiguity/error grading, including multiple field mismatches, missing proposal, unusable input, and exclusions; unnecessary temporal clarification of a clear request is already confirmed unsuccessful.
- Approve the 50-DAU arrival profile, concurrency and bursts, run duration, percentile method, and cold/warm definitions.
- Separately authorize actual host/provider measurement and its budget; this draft and the local test suite do not supply that authorization.
