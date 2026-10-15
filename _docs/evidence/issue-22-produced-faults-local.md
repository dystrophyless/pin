# Focused Issue 22 producer-to-snapshot qualification

Result: PASS, limited to the local synthetic producer-to-operations scenario. Not overall Issue 22 acceptance.

- Tested revision: `9289fbc2cb90237f836ebb70f2f898b6b94b62c7`; tree `ab6a761aa8a0d95822a36859162819ee4f8dfb59`.
- Production subtree: `src/pin` tree `9d574847795a5ea13a9c41bee74f77e09557daf2`, 219 tracked files with per-file SHA-256 in `source-provenance.json`. Test file SHA-256: `20370857c881367ebccbcae57bdd58f9bfbe71221e0e8f82f5f639b3d9a8e198`.
- Receipt-time repository head: `62fb56fec84240de438ccc5ea58bc44b85ad1a9d`; source subtree and all 219 source hashes remained identical to tested provenance.
- Pytest: 5 passed, 0 failed, 0 errors, 0 skipped; 12.18 seconds. JUnit: `pytest.xml`. Ruff: PASS. Mypy: PASS, 220 files. Diff check: PASS.
- Scenario: actual local ingress/application-job/capture/provider-accounting/outbox/delivery-attempt/operations-read paths; fake transports only at external completion and Telegram boundaries. Both provider and delivery outcomes persist as unknown, appear in the snapshot by counts and opaque IDs, are not retried for the bounded unknown delivery result, and snapshot projection is read-only and redacted.
- Native fixture: `pin_issue22_ops_faultproducer_20261016_retry_4c114cca`. Pre-create exact non-template baseline: 174 databases. Post-drop and independent audit: exact baseline restored, fixture absent, protected databases present.
- Earlier clock-harness failure and its preserved packet: `C:\Users\roman\AppData\Local\Temp\pin-issue22-produced-faults-0578951-20261016-fe8614f5`. Corrected successful packet: `C:\Users\roman\AppData\Local\Temp\pin-issue22-produced-faults-0578951-20261016-retry1-4c114cca`.
- Limits: local/synthetic only; no external provider or Telegram calls. No hosted alert, low-disk, restore, or host recovery proof. Not a full-suite run or overall product acceptance.

See the packet's `README.md` and `qualification-result.json` for the detailed receipt and its command logs, JUnit, provenance manifest, fixture manifest, cleanup receipt, and independent catalog audit.

Root direct verification:inspected the composed producer-to-snapshot test, raw5-case JUnit (0failures/errors/skips,12.184s), current test SHA256 and all219production-file hashes. Root independently passed Ruff/mypy and compared the live174database names/owners/connection flags exactly against pre-create baseline before granting the next native slot; the prior fixture was absent and protected3present. The initial stale-clock harness failure remains preserved; no production code changed and no558-case full-suite claim follows.
