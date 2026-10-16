from __future__ import annotations

import json
from types import SimpleNamespace
from typing import NoReturn

import pytest

from pin.entrypoints import operations


@pytest.fixture
def platform_independent_host_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pin.entrypoints.operations.os.cpu_count", lambda: 2)
    monkeypatch.setattr(operations, "_cpu_busy_percent", lambda: 0.0)
    monkeypatch.setattr("pin.entrypoints.operations.sys.platform", "linux")


def test_host_resources_exposes_measured_low_disk_space_without_a_threshold(
    monkeypatch: pytest.MonkeyPatch,
    platform_independent_host_resources: None,
) -> None:
    monkeypatch.setattr(
        "pin.entrypoints.operations.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=99, free=1),
    )

    result = operations.read_host_resources()

    assert result["disk_total_bytes"] == 100
    assert result["disk_free_bytes"] == 1
    assert result["disk_error"] is None


def test_host_resources_reports_redacted_disk_measurement_failure(
    monkeypatch: pytest.MonkeyPatch,
    platform_independent_host_resources: None,
) -> None:
    def fail_disk_usage(_path: object) -> NoReturn:
        raise OSError(28, "No space left on device", "private-volume-path")

    monkeypatch.setattr(
        "pin.entrypoints.operations.shutil.disk_usage",
        fail_disk_usage,
    )

    snapshot = json.dumps(operations.read_host_resources())

    assert '"disk_total_bytes": null' in snapshot
    assert '"disk_free_bytes": null' in snapshot
    assert '"disk_error": "OSError"' in snapshot
    assert "No space left on device" not in snapshot
    assert "private-volume-path" not in snapshot
