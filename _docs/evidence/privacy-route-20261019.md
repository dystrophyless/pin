# Privacy route proof — 2026-10-19

This evidence records the local working-tree verification of the bilingual `/privacy` route. The tested source was based on revision `abee1c5` with the four files below modified or added in the working tree. The guarded native runner recorded exit code 0, unchanged source during the run, successful removal of its disposable fixture, and an exact match to the pre-run database catalog. The raw test log, JUnit XML, and runner manifest are retained in the local proof packet.

## Tested source

SHA-256 values for the four privacy-slice files at verification time:

| File | SHA-256 |
|---|---|
| `src/pin/adapters/telegram/application_worker.py` | `27866FDD69FCC60DB879D8F476C749E76861C808CBF658DB298D3386971D3948` |
| `src/pin/adapters/telegram/presentation/templates.py` | `6F6F24585A67B1B4452DEBF5AE6EE91F299365EA20B2CE3D8EFF5EAD055FF005` |
| `tests/test_privacy_presentation.py` | `5D0348A513F4D0879EC5E39BC307012B3FCADEE6F3DC2336B67E608D5AEFB3DE` |
| `tests/test_payment_support_routes_postgres.py` | `D1AC4F98C7638FEEC9D948A748925FC06435A0C6AB1A6548CA2A55048067880E` |

## Results

- Guarded native run of `tests/test_payment_support_routes_postgres.py`: **2 passed, 0 failed, 0 errors, 0 skipped** in 6.98 seconds. The tests were `test_bilingual_terms_acceptance_and_payment_support_are_persisted_routes` and `test_privacy_is_a_durable_private_command_for_every_access_state`.
- Root's separate offline suite: **21 passed** in 4.66 seconds.
- Ruff passed for the assigned privacy files; focused mypy passed for `tests/test_payment_support_routes_postgres.py`; pytest collection found both native-module tests; scoped `git diff --check` passed.

The privacy route test covers six states: no saved profile, an in-progress Russian onboarding draft, expired Russian and English trial access, and exhausted Russian and English trial allowances. It verifies localized replies and the privacy disclosure without assistant invocation or changes to the owner's snapshotted domain records. It also verifies duplicate-update idempotency against the persisted application-job key and rejects group-message ingress without persisting an inbox row.

Earlier native attempts exposed test-fixture contract mistakes around quota exhaustion, job linkage, ingress receipt fields, and the ignored group-message return. Those assertions were corrected in the test file only. The final run above completed with zero skips; earlier failures are not product failures or final-run skips.

## Limits

This is local, uncommitted working-tree evidence. It does not establish deployment or a live Telegram journey. Explicit processing consent remains unimplemented; reading `/privacy` is not recorded as consent. The applicable statutory payment-retention period remains unverified, so the legal-retention gate is still open.