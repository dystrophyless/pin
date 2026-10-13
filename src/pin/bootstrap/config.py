"""Environment-backed configuration with secret-safe validation errors."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


class ProcessRole(StrEnum):
    GATEWAY = "gateway"
    APP_WORKER = "app-worker"
    DELIVERY_WORKER = "delivery-worker"
    SPEECH_WORKER = "speech-worker"


class SettingsError(ValueError):
    """A configuration error whose message contains field names, never values."""


@dataclass(frozen=True, slots=True)
class SmokeSettings:
    """Secrets and exact destination for the opt-in Telegram smoke sender."""

    bot_token: SecretStr
    test_chat_id: int


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_url: Annotated[SecretStr, Field(min_length=1)]
    bot_token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    telegram_ingress_mode: Literal["webhook", "polling"] = "webhook"
    gateway_host: Annotated[str, Field(min_length=1)] = "127.0.0.1"
    gateway_port: Annotated[int, Field(ge=1, le=65535)] = 8080
    openrouter_api_key: SecretStr | None = None

    @field_validator("bot_token")
    @classmethod
    def telegram_token_format(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"\d{5,}:[A-Za-z0-9_-]{20,}", value.get_secret_value()
        ):
            raise ValueError("must be a Telegram bot token")
        return value

    @field_validator("openrouter_api_key")
    @classmethod
    def provider_key_not_short(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().strip()) < 20:
            raise ValueError("must contain at least 20 characters")
        return value

    @field_validator("webhook_secret")
    @classmethod
    def webhook_secret_format(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not 32 <= len(secret) <= 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", secret):
                raise ValueError("must be a 32-256 character webhook secret")
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def postgres_url_required(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError):
            raise ValueError("must be a valid PostgreSQL URL") from None
        if (
            parsed.scheme not in {"postgresql", "postgresql+psycopg"}
            or not parsed.hostname
            or not parsed.path.strip("/")
        ):
            raise ValueError("must be a PostgreSQL URL with a host and database")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("must use a valid PostgreSQL port")
        return value


def load_settings(
    role: ProcessRole,
    environ: Mapping[str, str] | None = None,
    *,
    project_root: Path | None = None,
) -> Settings:
    """Load only PIN_* settings and enforce the secrets needed by this role."""
    source = os.environ if environ is None else environ
    names = {
        "database_url": "PIN_DATABASE_URL",
        "bot_token": "PIN_BOT_TOKEN",
        "webhook_secret": "PIN_WEBHOOK_SECRET",
        "telegram_ingress_mode": "PIN_TELEGRAM_INGRESS_MODE",
        "gateway_host": "PIN_GATEWAY_HOST",
        "gateway_port": "PIN_GATEWAY_PORT",
        "openrouter_api_key": "PIN_OPENROUTER_API_KEY",
    }
    values = {field: source[name] for field, name in names.items() if source.get(name)}
    if (
        role is ProcessRole.GATEWAY
        and values.get("telegram_ingress_mode", "webhook") == "polling"
        and "bot_token" not in values
    ):
        smoke_settings = load_smoke_settings(project_root=project_root, environ=source)
        values["bot_token"] = smoke_settings.bot_token.get_secret_value()
    try:
        settings = Settings.model_validate(values)
    except ValidationError as error:
        failures = ", ".join(
            f"{names.get(str(item['loc'][0]), str(item['loc'][0]))} ({item['type']})"
            for item in error.errors(include_input=False, include_context=False)
        )
        raise SettingsError(f"Invalid configuration: {failures}") from None

    required_by_role = {
        ProcessRole.GATEWAY: ("bot_token",),
        ProcessRole.APP_WORKER: ("openrouter_api_key",),
        ProcessRole.DELIVERY_WORKER: ("bot_token",),
        ProcessRole.SPEECH_WORKER: (),
    }
    missing = [
        names[field]
        for field in required_by_role[role]
        if getattr(settings, field) is None
        or not getattr(settings, field).get_secret_value().strip()
    ]
    if missing:
        raise SettingsError(f"Missing required configuration: {', '.join(missing)}")
    if role is ProcessRole.GATEWAY and settings.telegram_ingress_mode == "webhook":
        if settings.webhook_secret is None:
            raise SettingsError("Missing required configuration: PIN_WEBHOOK_SECRET")
    if settings.webhook_secret and len(settings.webhook_secret.get_secret_value()) < 32:
        raise SettingsError("Invalid configuration: PIN_WEBHOOK_SECRET (too_short)")
    return settings


def load_smoke_settings(
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> SmokeSettings:
    """Load smoke-only settings; environment values override ignored `.env.local`."""
    source = os.environ if environ is None else environ
    local_values: dict[str, str] = {}
    config_path = (project_root or Path.cwd()) / ".env.local"
    if config_path.is_file():
        local_values = _parse_smoke_local_config(config_path.read_text(encoding="utf-8"))

    token = source.get("PIN_BOT_TOKEN") or local_values.get("PIN_BOT_TOKEN")
    chat_value = source.get("PIN_TEST_CHAT_ID") or local_values.get("PIN_TEST_CHAT_ID")
    if not token or not chat_value:
        raise SettingsError("configure PIN_BOT_TOKEN and PIN_TEST_CHAT_ID in .env.local")
    if any(character.isspace() for character in token):
        raise SettingsError("PIN_BOT_TOKEN has an invalid format")
    try:
        chat_id = int(chat_value)
    except ValueError:
        raise SettingsError("PIN_TEST_CHAT_ID must be a private-chat numeric ID") from None
    if chat_id == 0:
        raise SettingsError("PIN_TEST_CHAT_ID must be a non-zero private-chat numeric ID")
    return SmokeSettings(SecretStr(token), chat_id)


def _parse_smoke_local_config(content: str) -> dict[str, str]:
    """Read only the smoke settings from simple KEY=VALUE lines."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or key not in {"PIN_BOT_TOKEN", "PIN_TEST_CHAT_ID"}:
            continue
        if value.startswith(('"', "'")) and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        if key in values:
            raise SettingsError(f"duplicate local setting on line {line_number}")
        values[key] = value
    return values
