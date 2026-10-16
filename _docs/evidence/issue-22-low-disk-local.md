# Issue 22 local low-disk diagnostic evidence

## Result

The local host projection already reports `disk_total_bytes` and `disk_free_bytes`, and the operations runbook already describes how to investigate low measured free space. A failed disk measurement previously raised out of the reporter; in the snapshot fallback it could be queried a second time and prevent JSON output entirely.

The reporter now preserves the snapshot when disk usage raises `OSError`: both byte values are `null`, and `disk_error` contains only the exception class. A successful measurement emits both byte values and `disk_error: null`. No threshold or low-space classification was added. The runbook explains that unavailable measurements require a normal host-level check and must not be interpreted as adequate or low space.

## Verification

On the Windows local checkout, with the frozen environment and offline mode:

- `uv run --frozen --offline python -m pytest -q tests/test_operations_host_resources.py --junitxml <evidence>\junit.xml` — **2 passed**. The real `read_host_resources()` function was exercised with injected 1-byte free space and injected `OSError` carrying a private path and message. The test verifies measured values remain visible and error details are redacted.
- `uv run --frozen --offline ruff check src/pin/entrypoints/operations.py tests/test_operations_host_resources.py` — **passed**.
- `uv run --frozen --offline mypy src/pin/entrypoints/operations.py tests/test_operations_host_resources.py` — **passed**.
- `uv run --frozen --offline pin-architecture-check` — **passed**.
- `git diff --check` — **passed**.

The first pytest command attempt used an incorrectly formatted JUnit path and collected no tests; its raw output is retained. The corrected command above is the passing result. All disk responses were injected; no disk was filled and no database, provider, or Telegram transport was used.

## Limits

This is an offline projection/failure-containment check. It does not simulate real host disk pressure, establish an alert threshold, or verify host alert delivery. The snapshot remains a point-in-time local diagnostic.
