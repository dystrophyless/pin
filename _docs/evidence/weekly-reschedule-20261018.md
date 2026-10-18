# Weekly reschedule producer verification — 2026-10-18

## Requirement and failure

The real hosted client created a three-occurrence Monday series, explicitly completed the first occurrence, and then selected the second through `/reschedule` → This and future → a Wednesday replacement date. The bot could not prepare a preview. A read-only check showed no partial date, count or history change. See [client observations](telegram-dialogue-20261018.md#series-completion-and-new-reschedule-finding).

The application producer passed a new anchor date with the old Monday rule to the recurrence exception API. Calendar validation correctly rejected a first occurrence that did not match its selected weekday. A fresh PostgreSQL regression reproduced that exact failure through the command/callback producer before the fix; the original failing JUnit/log are retained privately.

The owner then confirmed the multi-weekday policy in [SPEC-001 v0.19, TR-02](../specifications/SPEC-001_pin_mvp.md): Monday/Wednesday moved from Monday to Tuesday becomes Tuesday/Wednesday. Other weekdays are preserved, rather than rotating the whole pattern.

## Change

The shared mutation application producer supplies the existing `new_pattern` field for weekly This and future changes. It recovers the selected occurrence's original pattern weekday from its series sequence and anchor, then replaces that weekday with the requested date's weekday. This also handles an occurrence previously moved individually. The existing interval, count/end condition, confirmation, owner/version checks, bounded projection, history and stable identity contracts remain in force. Monthly-pattern behavior is unchanged.

The preview uses the existing projection to show localized resulting weekdays, weekly interval and remaining/total count alongside affected dates. The same application producer is used when confirming, so a stale projection is still rejected.

## Native evidence

The complete `tests/test_mutation_runtime_postgres.py` module passed **19 tests, zero failures/errors/skips** on a fresh guarded PostgreSQL 18.6 fixture. Raw JUnit duration is 8.207 seconds. Base revision was `95bcf1c`, plus the then-uncommitted application/presentation/test changes. Root inspected the complete diff and raw JUnit, and matched the recorded source hashes to the working tree at that point.

The six new producer-level cases cover:

- The observed Monday-to-Wednesday edit after one completed occurrence.
- English localized pattern/count preview.
- The approved June 1/15/29 → June 8/22/July 6 two-week reanchor.
- A prior single-occurrence exception followed by This and future.
- Exactly ten occurrences, four completed, six moved to Wednesdays with total ten retained.
- Monday/Wednesday → Tuesday/Wednesday, preserving the unselected weekday.

The cases traverse command, scope callback, typed correction, stored preview and explicit confirmation. They check previewed dates/pattern/count, absence of pre-confirmation state/version mutation, persisted dates, stable IDs/keys, completed history, the new anchor and the outbox's remaining-count value. Existing owner isolation, stale previews, cancellation and malformed-confirmation cases also passed in the same module.

The original product failure and intermediate harness/assertion failures are retained separately. English success-copy assertions were corrected without changing product behavior. A rerun against an already-cleaned fixture produced connection errors; it is not a product regression or accepted verification result.

Guarded cleanup used the owned fixture only, checked its owner and zero other sessions, and used an ordinary non-forced drop. Root independently queried the live catalog afterward: it exactly matched both baseline and cleanup records, all protected databases remained, and the fixture was absent. No provider, Telegram or payment call was made by these native tests.

## Limits and next step

The 19-case run covers the count-based weekly branches above. A subsequent presentation-only correction shows the end date for an end-date-limited series, reserving unbounded wording for a series with neither limit. Root inspected that final delta and independently ran all 17 presentation tests (zero failures/errors/skips), including finite/unbounded RU/EN copy. Final scoped Ruff, mypy on both production files and architecture checks passed. The earlier native run is not relabeled as including those later pure rendering branches.

The combined correction is committed and pushed as `b0898d5`. [CI/deployment 39148133401](https://github.com/dystrophyless/pin/actions/runs/39148133401) passed, including temporary SSH cleanup. Root read that exact active AWS release and host-health success/exit zero.

## Hosted client retest

Root repeated the original deterministic `/reschedule` selection on that release. The rendered preview showed 26 October → 28 October at 18:00 Asia/Almaty, affected dates 28 October/4 November, Wednesday repetition and two remaining/three total. A read-only query while that preview was visible still showed the old Monday dates, open/version 1 and the original completed/version-2 history. Only then did root press Reschedule.

The client returned Schedule updated. The subsequent read-only query showed open/version-2 occurrences on 28 October and 4 November at 13:00 UTC, anchor 28 October, weekdays `[2]`, and total count 3. The first 19 October occurrence remained completed/version 2. The Notifications consumer had updated the four scheduled reminder intents to 27 October 13:00 UTC, 28 October 12:00 UTC, 3 November 13:00 UTC and 4 November 12:00 UTC. The completed occurrence's one reminder remained invalidated. These are persisted scheduling/consumer observations, not claims that those future reminders were delivered.

Root selected the 28 October synthetic occurrence, chose This and future, reviewed a preview naming exactly 28 October/4 November, and confirmed cancellation. The client reported cancellation. The final read-only query showed both future occurrences cancelled/version 3, the first still completed/version 2, the standalone untimed item cancelled/version 2, and zero scheduled/five invalidated reminder intents. No unrelated owner plan was selected or modified.

The hosted preview also exposed untranslated reminder-unit labels. Presentation-only commit `d118529` replaces them with readable RU/EN calendar-day/hour/minute/second units while retaining projected due times and event-zone conversion. Root reviewed that delta and reran all 17 presentation tests, plus scoped Ruff/mypy. [CI/deployment 39148742822](https://github.com/dystrophyless/pin/actions/runs/39148742822) passed, including SSH cleanup; root observed the active release and host-health success/exit zero. A later synthetic client wording check remains pending. No native rerun is attributed to this copy-only change. This report does not establish the complete core matrix, legal/provider eligibility or issue/release acceptance.
