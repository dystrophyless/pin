from types import SimpleNamespace

import pytest
from aiogram.types import MessageEntity

from pin.adapters.telegram.presentation.smoke import (
    _entity_summary,
    load_settings,
    main,
    validate_private_chat,
)


def test_dry_run_never_loads_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    def forbidden():
        raise AssertionError("credentials must not be accessed")

    assert main([], settings_loader=forbidden) == 0
    assert "no Telegram API calls" in capsys.readouterr().out


def test_network_errors_do_not_expose_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    def failed():
        raise RuntimeError("TOP-SECRET")

    assert main(["--send"], settings_loader=failed) == 2
    assert "TOP-SECRET" not in capsys.readouterr().out


def test_only_exact_private_destination_is_accepted() -> None:
    validate_private_chat(SimpleNamespace(id=123, type="private"), 123)
    for chat in [SimpleNamespace(id=124, type="private"), SimpleNamespace(id=123, type="group")]:
        with pytest.raises(ValueError):
            validate_private_chat(chat, 123)


def test_string_entity_types_are_summarized() -> None:
    message = SimpleNamespace(entities=[MessageEntity(type="bold", offset=0, length=2)])
    assert _entity_summary(message) == "bold:1"


def test_missing_local_credentials_fail_without_values(tmp_path) -> None:
    with pytest.raises(ValueError, match="configure PIN_BOT_TOKEN"):
        load_settings(project_root=tmp_path, environ={})


def test_environment_smoke_settings_override_local_file(tmp_path) -> None:
    (tmp_path / ".env.local").write_text(
        "PIN_BOT_TOKEN=local-secret\nPIN_TEST_CHAT_ID=111\n", encoding="utf-8"
    )

    settings = load_settings(
        project_root=tmp_path,
        environ={"PIN_BOT_TOKEN": "environment-secret", "PIN_TEST_CHAT_ID": "222"},
    )

    assert settings.bot_token.get_secret_value() == "environment-secret"
    assert settings.test_chat_id == 222
    assert "environment-secret" not in repr(settings)


def test_ignored_local_file_supplies_smoke_settings(tmp_path) -> None:
    (tmp_path / ".env.local").write_text(
        "PIN_BOT_TOKEN='local-secret'\nPIN_TEST_CHAT_ID=333\n"
        "PIN_DATABASE_URL=ignored\n",
        encoding="utf-8",
    )

    settings = load_settings(project_root=tmp_path, environ={})

    assert settings.bot_token.get_secret_value() == "local-secret"
    assert settings.test_chat_id == 333
