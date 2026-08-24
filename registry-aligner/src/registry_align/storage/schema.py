"""Alembic lifecycle helpers used by the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from alembic import command


def alembic_config() -> Config:
    config = Config()
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    config.set_main_option("script_location", str(migrations))
    return config


def upgrade_schema(engine: Engine) -> None:
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def schema_status(engine: Engine) -> dict[str, Any]:
    config = alembic_config()
    expected = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return {"current": current, "expected": expected, "current_is_head": current == expected}
