# Issue #7: confirmed onboarding and preferences

Date: 2026-10-13 (Asia/Qyzylorda). Accepted implementation revision: `353e57d464e9650d130087240faf1cdf64667f3f`. Local Windows development only; no push or deployment.

## Source and environment

Parent exported the committed index to `C:/Users/roman/AppData/Local/Temp/pin-parent-onboard-f0e7/source`. Python import provenance in regression and Telegram proof points to that snapshot, excluding any later working-tree edits. Interpreter: the locked project CPython 3.12.12 environment; PostgreSQL 18.6 at loopback port 51017. Cluster ownership was checked with `SHOW data_directory` against `C:/Users/roman/AppData/Local/Temp/pin-issue2-eeadfd1-20261013` before creating or dropping fixtures. The installed Windows PostgreSQL service was not modified.

## Direct parent verification

- Full committed-source `python -m pytest -q`: **147 passed, no skips, 63.85 seconds** (chunk `68ea72`). Ruff passed; mypy passed on 75 source files; architecture import boundaries passed in the same runner. Eight isolated PostgreSQL fixture URLs were supplied through the documented test variables.
- Initial full run: 142 passed, five model tests failed because their empty fixture lacked its schema. The parent pre-migrated both presentation and model fixtures and reran without product-code or test changes. The initial log is retained as `initial-suite-schema-setup-failure.log` in the proof directory; it is not accepted evidence.
- Alembic `upgrade head`, `current`, `check`, `downgrade base`, `upgrade head`, `check`: passed; head `billing_trial_activation_0001`, no drift both times (chunks `9446d2`, `3918e6`). The chain includes `conversation_model_0002 -> identity_profile_0001 -> billing_trial_activation_0001`.
- Additional actual-process restart proof: **16 separate child worker processes**, eight updates per RU/EN owner. Every step resumed its persisted draft generation; there was no profile/trial before confirmation, and exactly one profile/version/trial/period after confirmation, repeated confirmation, and returning `/start` (chunk `ea6e57`). Each child uses the same committed ingress/application handler, trusted mapping, durable job and transaction path. This supplements the session-reconstruction tests with new OS processes; it does not simulate every possible crash point.
- All nine newly parent-owned fixture databases were dropped and their absence verified against the guarded cluster (chunk `2f31e7`). Source snapshot and temporary proof runners remain available; the historical live model-accounting database was untouched.

## Requirement coverage

| Required behavior | Evidence |
|---|---|
| Language -> explicit IANA zone -> reminder suggestions -> quiet/review suggestions -> final confirmation | Six onboarding PostgreSQL scenarios plus process-by-process RU/EN proof |
| Suggestions require choice; invalid zones, offsets, times and quiet intervals require correction | Persisted negative fixtures assert unchanged step and no activated trial |
| Atomic profile, one trial, receipt and outbox; abandoned setup has no trial | Ingress/application PostgreSQL fixtures, cancellation, same-UoW failure after Billing write, concurrent confirmation and replay |
| Restart and retained-user preferences; preview cancellation/version conflict | Persisted dialogs, fresh-process proof and simultaneous/version-conflict fixtures |
| Suitable localized buttons, safe owner/version/generation binding | Real durable callback fixtures for foreign, forged, duplicate and stale tokens; canceled/recreated draft rejects an old confirmation even at the same step/version; completed-profile Edit button works |
| Published preference snapshot and home-zone handoff | Identity API and versioned `identity.home_zone_change.requested` event assertions; no existing-plan shift is implemented here |
| Minimal prior-trial facts; active paid restoration | Billing suite verifies missing-account signup, replay/concurrency, identity mismatch, marker survival after account removal, active-paid period/usage preservation and expired return. A deleted active trial is suppressed while ordinary read/complete/cancel rights return; no new trial is granted |

The six onboarding PostgreSQL tests and 29 Billing unit/PostgreSQL tests are included in the parent full suite. Domain logic remains framework-independent; cross-context consumers use published contracts. Profile confirmation and Billing activation share one SQLAlchemy session/transaction.

## Real Telegram rendering

The owner-authorized dedicated bot/private destination was loaded through the existing ignored local configuration bootstrap and validated as the exact private chat. Synthetic updates traversed the real persisted ingress/application path; the parent sent the exact generated `telegram.reply.requested` payloads and keyboards with the established presentation sender and typing helper.

Telegram acknowledged RU message **8** and EN message **9**, each with one bold entity, one code entity and a bot-command entity. Returned keyboard payloads matched exactly: `Подтвердить / Отмена` and `Confirm / Cancel` (chunk `7ccf13`). No token or chat identifier is recorded. Each unsaved synthetic setup was canceled; no plan or trial was created by the render smoke.

This is actual API parsing/rendering and generated-button evidence, plus separate durable handler/callback tests. It is not a full live user dialogue, a production reply-delivery loop, a read receipt, or new human client-render confirmation. The previously accepted #4 client-render confirmation remains presentation-foundation evidence; no repeated per-issue human acceptance was requested.

## Acceptance and limits

Direct Orchestrator verification accepts #7's onboarding/preferences increment for the current Windows local-development phase. Replies and versioned handoffs are durably queued; delivery implementation remains #12 and existing-plan timezone effects remain #15. Account-deletion coordination and backup/return integration remain #23; these tests prove the published Billing return-policy seam, not a completed deletion flow. No guessed zone, paid provisioning, provider/model call, Docker, hosting, Whisper accuracy, release or overall MVP acceptance is implied. The prior eight-call model smoke budget remains exhausted.

Publisher read back #7 closed/completed with `status:accepted`: [acceptance comment](https://github.com/dystrophyless/pin/issues/7#issuecomment-6145769892). #8 was then marked ready after live prerequisite checks and dispatched under the active implementation goal.
