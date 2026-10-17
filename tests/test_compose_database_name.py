from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[1]
_COMPOSE_FILE = _REPO_ROOT / "compose.yaml"


def _compose_model(tmp_path: Path, *, database_name: str | None) -> dict[str, object]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is required to render the deployment config")

    env_file = tmp_path / "runtime.env"
    values = [
        "PIN_DB_PASSWORD=synthetic-only",
        "PIN_BOT_TOKEN=synthetic-only",
        "PIN_OPENROUTER_API_KEY=synthetic-only",
    ]
    if database_name is not None:
        values.append(f"PIN_DATABASE_NAME={database_name}")
    env_file.write_text("\n".join(values) + "\n", encoding="utf-8")

    environment = os.environ.copy()
    environment.pop("PIN_DATABASE_NAME", None)
    result = subprocess.run(
        [
            docker,
            "compose",
            "--profile",
            "ops",
            "--env-file",
            str(env_file),
            "--file",
            str(_COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, "Docker Compose could not render compose.yaml"
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("configured_name", "expected_name"),
    [(None, "pin"), ("pin_recovery_cutover", "pin_recovery_cutover")],
)
def test_compose_database_name_is_shared_by_runtime_and_postgres(
    tmp_path: Path, configured_name: str | None, expected_name: str
) -> None:
    model = _compose_model(tmp_path, database_name=configured_name)
    services = model["services"]
    postgres = services["postgres"]
    assert postgres["environment"]["POSTGRES_DB"] == expected_name
    assert "ports" not in postgres
    expected_url_suffix = f"@postgres:5432/{expected_name}"
    for service_name in ("gateway", "app-worker", "retention", "migrate"):
        assert services[service_name]["environment"]["PIN_DATABASE_URL"].endswith(
            expected_url_suffix
        )
    assert "POSTGRES_DB" in postgres["healthcheck"]["test"][1]
