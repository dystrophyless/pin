from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.skipif(
    not os.getenv("PIN_TEST_MIGRATION_DATABASE_URL"),
    reason="set PIN_TEST_MIGRATION_DATABASE_URL to a fresh disposable PostgreSQL database",
)
def test_fresh_migration_downgrade_and_reupgrade_lifecycle() -> None:
    database_url = os.environ["PIN_TEST_MIGRATION_DATABASE_URL"]
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "persistence_0001")
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "action_receipts" in inspector.get_table_names(schema="pin_persistence")
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "persistence_0001"
            )

        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert "pin_persistence" not in inspect(connection).get_schema_names()
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM alembic_version"
            ).scalar_one() == 0

        command.upgrade(config, "persistence_0001")
        with engine.connect() as connection:
            assert "action_receipts" in inspect(connection).get_table_names(
                schema="pin_persistence"
            )
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "persistence_0001"
            )
    finally:
        engine.dispose()
