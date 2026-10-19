# Processing-consent Identity local evidence — 2026-10-19

The reviewed Identity/onboarding increment for issue #29 passed its delegated local acceptance run against the final source. Guarded-processing work in #30 is in progress; the integrated gate in #31 remains pending. This record does not claim deployed consent enforcement, legal approval, or overall release acceptance.

## Native verification — 14 native cases + 7 presentation cases

The Orchestrator ran the focused PostgreSQL and presentation modules with the repository virtual environment. The redacted command shape was:

```text
.venv/Scripts/python.exe -m pytest tests/test_identity_onboarding_postgres.py tests/test_privacy_presentation.py tests/test_payment_support_routes_postgres.py -q --junitxml=<redacted-R4-packet>/pytest.xml -o cache_dir=<redacted-R4-packet>/pytest-cache
```

Result: **21 passed, 0 failed, 0 errors, 0 skipped**. Console duration was 51.44 seconds; JUnit duration was 50.888 seconds. The suite contained 12 onboarding/Identity tests, 7 privacy-presentation tests, and 2 payment/support-route tests. The run exercised only the assigned bounded fixtures; it is not a full-suite or service-quality result.

Independent checks reported by the Orchestrator also passed:

- Ruff on the changed Python paths.
- `.venv/Scripts/python -m mypy src` — 222 source files.
- `.venv/Scripts/python -m pin.architecture`.
- `git diff --check`.

## Acceptance coverage

| Criterion | Focused evidence |
|---|---|
| AC-01 — durable required/granted/revoked state, provenance, deny-by-default, revisions and regrant | `test_processing_consent_persists_revisions_regrant_invalidation_and_purge`; `test_authorization_does_not_default_allow_when_identity_storage_fails`; `test_migration_does_not_backfill_existing_profiles` |
| AC-02 — explicit RU/EN onboarding action, callback binding, no plain-text consent, replay/stale/foreign rejection, and transaction rollback | `test_private_onboarding_survives_restarts_and_activates_one_trial`; `test_initial_consent_details_and_callback_failure_roll_back_all_effects`; the RU/EN explicit-action cases in `test_privacy_presentation.py` |
| AC-03 — Details and `/privacy` before profile creation, unchanged draft, and existing-profile controls | `test_initial_consent_details_and_callback_failure_roll_back_all_effects`; `test_privacy_is_a_durable_private_command_for_every_access_state`; bilingual privacy presentation cases |
| AC-04 — missing-row grant/revoke serialization and authorization/withdrawal fence through commit | `test_missing_row_grant_and_revoke_race_is_serialized`; `test_authorization_and_withdrawal_hold_the_owner_fence_through_commit`. Native sessions observed PostgreSQL `pg_blocking_pids()` in both lock orders; the reverse order revoked a newly regranted active tuple and rejected authorization after commit. |
| AC-05 — Identity purge and restored-consent invalidation | `test_processing_consent_persists_revisions_regrant_invalidation_and_purge`; `test_migration_does_not_backfill_existing_profiles` |
| AC-06 — existing onboarding, privacy/help/support and Billing distinctions | `test_cancel_and_preview_are_non_mutating_and_zone_change_is_handed_off`; `test_privacy_is_a_durable_private_command_for_every_access_state`; `test_bilingual_terms_acceptance_and_payment_support_are_persisted_routes`; all 7 presentation cases |

The callback-failure case verifies rollback of the callback token, consent, preferences, and trial within the caller transaction. The tests also cover large Telegram actor IDs, stale policy/draft/owner cases, duplicate handling, unchanged existing-profile preferences/access, and migration without a grant backfill.

## Corrections retained in the test history

An early native run exposed that Telegram actor IDs were being persisted as a 32-bit integer; the ORM model and not-yet-deployed migration now use the existing 64-bit pattern. Several subsequent failures were fixture-contract mistakes: reading an already-unwrapped event payload as nested, reusing an update ID, and asserting fields not present on the persisted job/receipt contracts. The final lock test was strengthened to use a fresh regrant in its reverse race and observe actual database blocking rather than infer it from a scheduling delay. The final R4 run passed with no skips.

## Source boundary and cleanup

The checked-out branch was `main` at documentation baseline `23278d3b88fa07c55c563b0cca8b5e4d7c5402e8`. The native manifest recorded source unchanged during execution, successful test exit, both exact test fixtures ordinarily dropped, and the post-run catalog equal to its captured baseline. No source was edited during the native run.

Product files remain local and uncommitted: the Telegram application worker and onboarding/privacy presentation, Identity public/application/model/store files, the consent migration and application capability, and the focused onboarding, privacy, and payment-support tests. The existing unrelated evidence drafts, local inquiry draft, and untracked `output/` were preserved. No deployment, provider call, payment/Stars test, remote issue update, commit, or push is represented here.

SHA-256 values captured in the R4 source manifest:

| Source file | SHA-256 |
|---|---|
| `src/pin/adapters/telegram/application_worker.py` | `d3776d3f5268c36f3d5080e7e3e699863a800626594ed3e745612e12a9a5e7f2` |
| `src/pin/adapters/telegram/presentation/onboarding.py` | `7674fc2348396fb8e714e9399f0bde520ad7d80e6504cffcc6656515f66470f5` |
| `src/pin/adapters/telegram/presentation/templates.py` | `6f6f24585a67b1b4452debf5ae6ee91f299365ea20b2ce3d8eff5ead055ff005` |
| `src/pin/contexts/identity/api.py` | `2bc4dc103860a8e068a7e5cf2c2bd226276150a7c502748a6fb571865d1125a9` |
| `src/pin/contexts/identity/application/contracts.py` | `a87900d5966326b171097232999af53ad14fe3bf5687988ee13b5cf8de0a4ae9` |
| `src/pin/contexts/identity/application/onboarding.py` | `14bf0bf8e458b2cfe8be911cb63c90155381a048d5fbd7c085e3009e9bf62f15` |
| `src/pin/contexts/identity/application/processing_consent.py` | `afd4e5e589abbe265750a80895d8d0c59b0b54082aa988b45cb57c9c5601a88e` |
| `src/pin/contexts/identity/infrastructure/models.py` | `f46c31ab4e8b65ac2855ca72273d38281762eda5d0fd29f2edfa6965d7fe06b1` |
| `src/pin/contexts/identity/infrastructure/store.py` | `18741f4c227bba6e4a8903800f2a7a0cdfed5505193aa3fe770c2502b143aa5d` |
| `migrations/versions/identity_processing_consent_0001.py` | `a56e6df5b691f023b7db7bac98d74e8b14399d8e47d020343d3c51a166be2514` |
| `tests/test_identity_onboarding_postgres.py` | `3afa3073501a43f8ced9c0b74f2fce750402a9e7cb63d49002945068b4c41e38` |
| `tests/test_privacy_presentation.py` | `2b461b21f7006a0c933c3573bf9b539eb708cfe180d09e9a95d8feec10e68971` |
| `tests/test_payment_support_routes_postgres.py` | `e295ccb9468d21ff979933786d5df0ede55fada868b7017bcfdfc4bac20fd070` |

## Remaining boundaries

Issue #29's Identity capability and Telegram controls are locally verified. Issue #30 still owns enforcement across every AI-processing path and recovery/lifecycle entry; issue #31 still owns the final integrated proof. Neither task is complete here. The mandatory Kazakhstan financial-retention gate remains unresolved; this evidence does not establish legal compliance, a retention period, deployment safety, or final bot-phase readiness.
