from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest

from pin.observability import JsonFormatter


def _record(message: object, *args: object) -> logging.LogRecord:
    record = logging.LogRecord("pin.worker", logging.ERROR, __file__, 1, message, args, None)
    return record


def test_unclassified_log_payload_is_redacted() -> None:
    secret = "task text with private details 123456:abcdefghijklmnopqrstuvwxyz"
    rendered = JsonFormatter().format(_record(secret))

    assert secret not in rendered
    assert "private details" not in rendered
    assert json.loads(rendered)["message"] == "unclassified_log_message_redacted"


def test_safe_operational_identifiers_remain_traceable() -> None:
    job_id = uuid4()
    rendered = JsonFormatter().format(
        _record("speech_job_failed: job_id=%s error=%s", job_id, "TimeoutError")
    )

    assert (
        json.loads(rendered)["message"] == f"speech_job_failed: job_id={job_id} error=TimeoutError"
    )


def test_free_text_log_argument_is_redacted_but_event_is_kept() -> None:
    transcript = "transcript contains a private appointment at 09:30"
    rendered = JsonFormatter().format(_record("worker_stopped: error=%s", transcript))

    assert transcript not in rendered
    assert json.loads(rendered)["message"] == "worker_stopped: error=<redacted>"


def test_credential_shaped_string_is_not_a_safe_log_argument() -> None:
    key = "a" * 48
    rendered = JsonFormatter().format(_record("worker_stopped: error=%s", key))

    assert key not in rendered
    assert json.loads(rendered)["message"] == "worker_stopped: error=<redacted>"


@pytest.mark.parametrize("private_word", ["private_task_text", "my_secret_password"])
def test_bare_identifier_shaped_message_is_redacted(private_word: str) -> None:
    rendered = JsonFormatter().format(_record(private_word))

    assert private_word not in rendered
    assert json.loads(rendered)["message"] == "unclassified_log_message_redacted"


def test_approved_static_event_is_preserved() -> None:
    rendered = JsonFormatter().format(_record("telegram_poll_persist_failed"))

    assert json.loads(rendered)["message"] == "telegram_poll_persist_failed"


def test_startup_configuration_diagnostics_keep_allowlisted_fields() -> None:
    rendered = JsonFormatter().format(
        _record(
            "startup_rejected: configuration category=%s fields=%s",
            "invalid_configuration",
            ("PIN_DATABASE_URL", "PIN_BOT_TOKEN"),
        )
    )

    assert json.loads(rendered)["message"] == (
        "startup_rejected: configuration category=invalid_configuration "
        "fields=PIN_DATABASE_URL, PIN_BOT_TOKEN"
    )


def test_startup_configuration_diagnostic_redacts_unclassified_fields() -> None:
    secret = "TOKEN-SECRET"
    rendered = JsonFormatter().format(
        _record(
            "startup_rejected: configuration category=%s fields=%s",
            "invalid_configuration",
            ("PIN_DATABASE_URL", secret),
        )
    )

    assert secret not in rendered
    assert json.loads(rendered)["message"] == (
        "startup_rejected: configuration category=invalid_configuration fields=<redacted>"
    )

    untrusted_category = JsonFormatter().format(
        _record(
            "startup_rejected: configuration category=%s fields=%s",
            "private_prompt_SECRET",
            ("PIN_DATABASE_URL",),
        )
    )
    assert "private_prompt_SECRET" not in untrusted_category
    assert json.loads(untrusted_category)["message"] == (
        "startup_rejected: configuration category=<redacted> fields=PIN_DATABASE_URL"
    )


def test_startup_speech_dependency_diagnostic_is_an_exact_static_event() -> None:
    message = "startup_rejected: Speech dependencies are missing; install the `speech` extra."
    rendered = JsonFormatter().format(_record(message))

    assert json.loads(rendered)["message"] == message
    freeform = JsonFormatter().format(_record(f"{message} TOKEN-SECRET"))
    assert "TOKEN-SECRET" not in freeform
    assert json.loads(freeform)["message"] == "unclassified_log_message_redacted"


def test_telegram_identifiers_are_not_safe_operational_log_fields() -> None:
    rendered = JsonFormatter().format(_record("telegram_event: chat_id=%s", 123456789))

    assert "123456789" not in rendered
    assert json.loads(rendered)["message"] == "unclassified_log_message_redacted"
