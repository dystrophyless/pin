"""Environment-backed configuration with secret-safe validation errors."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator


class ProcessRole(StrEnum):
    GATEWAY = "gateway"
    APP_WORKER = "app-worker"
    DELIVERY_WORKER = "delivery-worker"
    SPEECH_WORKER = "speech-worker"


class SettingsError(ValueError):
    """A configuration error whose message contains field names, never values."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_url: Annotated[SecretStr, Field(min_length=1)]
    bot_token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
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
) -> Settings:
    """Load only PIN_* settings and enforce the secrets needed by this role."""
    source = os.environ if environ is None else environ
    names = {
        "database_url": "PIN_DATABASE_URL",
        "bot_token": "PIN_BOT_TOKEN",
        "webhook_secret": "PIN_WEBHOOK_SECRET",
        "openrouter_api_key": "PIN_OPENROUTER_API_KEY",
    }
    values = {field: source[name] for field, name in names.items() if source.get(name)}
    try:
        settings = Settings.model_validate(values)
    except ValidationError as error:
        failures = ", ".join(
            f"{names.get(str(item['loc'][0]), str(item['loc'][0]))} ({item['type']})"
            for item in error.errors(include_input=False, include_context=False)
        )
        raise SettingsError(f"Invalid configuration: {failures}") from None

    required_by_role = {
        ProcessRole.GATEWAY: ("bot_token", "webhook_secret"),
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
    if settings.webhook_secret and len(settings.webhook_secret.get_secret_value()) < 32:
        raise SettingsError("Invalid configuration: PIN_WEBHOOK_SECRET (too_short)")
    return settings

