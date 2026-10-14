"""Minimal structured JSON logging contract for startup and worker records."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from uuid import UUID

_SAFE_FIELD = re.compile(r"([_a-z]+)=%s")
_SAFE_STATIC_EVENTS = frozenset(
    {
        "telegram_callback_ack_terminal_rejection",
        "telegram_poll_persist_failed",
        "telegram_pre_checkout_answer_deferred",
        "telegram_pre_checkout_answer_deadline_exhausted",
        "telegram_pre_checkout_answer_rejected",
        "telegram_stars_receipt_rejected",
    }
)
_SAFE_TEMPLATES = frozenset(
    {
        "application_job_failure_record_failed: job_id=%s",
        "delivery_iteration_failed: error=%s",
        "gateway_stopped: error=%s",
        "reminder_reconciliation_failed: error=%s",
        "runtime adapters are not implemented for role=%s",
        "speech_job_failed: job_id=%s error=%s",
        "speech_job_failure_record_failed: job_id=%s",
        "stars_refund_outcome_unknown error=%s",
        "startup_ready: role=%s",
        "startup_rejected: %s",
        "telegram_callback_ack_failed error=%s",
        "telegram_poll_request_failed error=%s",
        "telegram_pre_checkout_answer_unknown error=%s",
        "worker_heartbeat_failed: error=%s",
        "worker_heartbeat_stop_failed: error=%s",
        "worker_stopped: error=%s",
    }
)
_SAFE_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_EXCEPTION = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:Error|Exception|Lost|Conflict|Failure)$")
_SAFE_ENUMS = {
    "gateway",
    "app-worker",
    "delivery-worker",
    "speech-worker",
    "application",
    "delivery",
    "speech",
    "unknown",
    "confirmed",
    "failed",
}


def _safe_argument(field: str | None, value: object) -> str:
    if field == "job_id" and isinstance(value, UUID):
        return str(value)
    if field == "job_id" and isinstance(value, str) and _SAFE_UUID.fullmatch(value):
        return value
    if field == "role" and isinstance(value, str) and value in _SAFE_ENUMS:
        return value
    if field == "error" and isinstance(value, str) and _SAFE_EXCEPTION.fullmatch(value):
        return value
    return "<redacted>"


def _redacted_message(record: logging.LogRecord) -> str:
    template = record.msg if isinstance(record.msg, str) else ""
    if template in _SAFE_STATIC_EVENTS and not record.args:
        return template
    if template not in _SAFE_TEMPLATES:
        return "unclassified_log_message_redacted"
    if not record.args:
        return template
    arguments = record.args
    if isinstance(arguments, Mapping):
        values: Iterable[object] = tuple(arguments.values())
    elif isinstance(arguments, tuple):
        values = arguments
    else:
        values = (arguments,)
    fields = _SAFE_FIELD.findall(template)
    safe = tuple(
        _safe_argument(fields[index] if index < len(fields) else None, value)
        for index, value in enumerate(values)
    )
    try:
        return template % (safe[0] if len(safe) == 1 else safe)
    except (TypeError, ValueError):
        return "unclassified_log_message_redacted"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": _redacted_message(record),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
