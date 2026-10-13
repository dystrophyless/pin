from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pin.bootstrap.clock import DUE_POLL_INTERVAL_SECONDS
from pin.bootstrap.composition import build_runtime
from pin.bootstrap.config import ProcessRole, SettingsError, load_settings

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "postgresql+psycopg://pin:db-secret@localhost/pin"
ROLE_ENV = {
    "gateway": {
        "PIN_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        "PIN_WEBHOOK_SECRET": "w" * 32,
    },
    "app-worker": {"PIN_OPENROUTER_API_KEY": "provider-secret-12345678901234567890"},
    "delivery-worker": {"PIN_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"},
    "speech-worker": {},
}


@pytest.mark.parametrize("role", list(ProcessRole))
def test_each_role_composes_without_loading_whisper(role: ProcessRole) -> None:
    env = os.environ.copy()
    env.update({"PIN_DATABASE_URL": DATABASE, **ROLE_ENV[role.value]})
    result = subprocess.run(
        [sys.executable, "-m", f"pin.entrypoints.{role.value.replace('-', '_')}", "--check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stderr)["message"].startswith(f"startup_ready: role={role.value}")
    if role is not ProcessRole.SPEECH_WORKER:
        assert "faster_whisper" not in result.stderr


def test_missing_and_malformed_configuration_is_clear_and_redacted() -> None:
    with pytest.raises(SettingsError, match="PIN_DATABASE_URL") as missing:
        load_settings(ProcessRole.GATEWAY, {})
    assert "secret" not in str(missing.value)

    with pytest.raises(SettingsError) as malformed:
        load_settings(
            ProcessRole.GATEWAY,
            {
                "PIN_DATABASE_URL": "postgresql://user:TOP-SECRET@/missing",
                "PIN_BOT_TOKEN": "TOKEN-SECRET",
                "PIN_WEBHOOK_SECRET": "short-secret",
            },
        )
    assert "PIN_DATABASE_URL" in str(malformed.value)
    assert "TOP-SECRET" not in str(malformed.value)
    assert "TOKEN-SECRET" not in str(malformed.value)

    for invalid_port in ("notaport", "999999"):
        with pytest.raises(SettingsError, match="PIN_DATABASE_URL"):
            load_settings(
                ProcessRole.SPEECH_WORKER,
                {"PIN_DATABASE_URL": f"postgresql://user:TOP-SECRET@localhost:{invalid_port}/pin"},
            )


def test_invalid_process_configuration_exits_nonzero_without_printing_secrets() -> None:
    env = os.environ.copy()
    env.update(
        {
            "PIN_DATABASE_URL": "postgresql://user:TOP-SECRET@localhost:notaport/pin",
            "PIN_BOT_TOKEN": "TOKEN-SECRET",
            "PIN_WEBHOOK_SECRET": "w" * 32,
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "pin.entrypoints.gateway", "--check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    record = json.loads(result.stderr)
    assert "PIN_DATABASE_URL" in record["message"]
    assert "PIN_BOT_TOKEN" in record["message"]
    assert "TOP-SECRET" not in result.stderr
    assert "TOKEN-SECRET" not in result.stderr


def test_database_credentials_are_redacted_from_settings_repr() -> None:
    settings = load_settings(ProcessRole.SPEECH_WORKER, {"PIN_DATABASE_URL": DATABASE})
    assert "db-secret" not in repr(settings)
    assert "db-secret" not in settings.model_dump_json()


def test_role_required_secrets_are_enforced_without_echoing_them() -> None:
    with pytest.raises(SettingsError, match="PIN_WEBHOOK_SECRET") as error:
        load_settings(
            ProcessRole.GATEWAY,
            {"PIN_DATABASE_URL": DATABASE, "PIN_BOT_TOKEN": ROLE_ENV["gateway"]["PIN_BOT_TOKEN"]},
        )
    assert "123456" not in str(error.value)


def test_invalid_secret_formats_fail_without_echoing_values() -> None:
    with pytest.raises(SettingsError, match="PIN_WEBHOOK_SECRET") as webhook_error:
        load_settings(
            ProcessRole.GATEWAY,
            {
                "PIN_DATABASE_URL": DATABASE,
                "PIN_BOT_TOKEN": ROLE_ENV["gateway"]["PIN_BOT_TOKEN"],
                "PIN_WEBHOOK_SECRET": "short-secret",
            },
        )
    assert "short-secret" not in str(webhook_error.value)

    with pytest.raises(SettingsError, match="PIN_OPENROUTER_API_KEY") as provider_error:
        load_settings(
            ProcessRole.APP_WORKER,
            {"PIN_DATABASE_URL": DATABASE, "PIN_OPENROUTER_API_KEY": "short-provider"},
        )
    assert "short-provider" not in str(provider_error.value)


def test_clock_and_adopted_poll_interval() -> None:
    from pin.bootstrap.clock import SystemClock

    assert DUE_POLL_INTERVAL_SECONDS == 1.0
    assert SystemClock().now().tzinfo == UTC
    assert isinstance(SystemClock().now(), datetime)

    class FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 10, 13, tzinfo=UTC)

    clock = FixedClock()
    runtime = build_runtime(
        ProcessRole.SPEECH_WORKER,
        clock=clock,
        environ={"PIN_DATABASE_URL": DATABASE},
    )
    assert runtime.clock is clock
    assert runtime.clock.now() == datetime(2026, 10, 13, tzinfo=UTC)


def test_smoke_logs_are_structured_json_and_do_not_echo_secrets() -> None:
    env = os.environ.copy()
    env.update({"PIN_DATABASE_URL": DATABASE, **ROLE_ENV["gateway"]})
    result = subprocess.run(
        [sys.executable, "-m", "pin.entrypoints.gateway", "--check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    record = json.loads(result.stderr)
    assert record["message"] == "startup_ready: role=gateway"
    assert "123456" not in result.stderr


@pytest.mark.parametrize("module", ["gateway", "app_worker", "delivery_worker"])
def test_non_speech_entrypoint_import_does_not_import_whisper(module: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; import pin.entrypoints.{module}; "
            "assert not any(name.startswith('faster_whisper') for name in sys.modules)",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_speech_startup_fails_if_optional_backend_is_unavailable(monkeypatch, capsys) -> None:
    import builtins

    from pin.entrypoints._common import main_for

    original_import = builtins.__import__

    def without_speech(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_speech)
    monkeypatch.setenv("PIN_DATABASE_URL", DATABASE)
    assert main_for(ProcessRole.SPEECH_WORKER, ["--check"]) == 2
    assert "Speech dependencies are missing" in capsys.readouterr().err

