"""Upgrade existing immutable annotations without replacing their content."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from tests.database import test_runtime_role_postgresql as legacy_rows
from tests.database.test_core_first_use_postgresql import core_store  # noqa: F401

pytestmark = pytest.mark.postgresql


def test_label_migration_preserves_existing_annotation_rows(core_store, monkeypatch):  # noqa: F811
    store, factory = core_store
    engine = factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"]["registry_align"]
    assert schema.startswith("core_first_use_") and schema.endswith("_test")
    config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
    config.attributes["snapshot_schema"] = schema
    config.attributes["manage_ssh_tunnel"] = False
    command.downgrade(config, "20260830_0002_storage_uri")
    monkeypatch.setattr(legacy_rows, "SCHEMA", schema)
    with engine.begin() as connection:
        ids = legacy_rows._insert_complete_graph(connection)
        before = dict(
            connection.exec_driver_sql(f"SELECT * FROM {schema}.annotations").mappings().one()
        )
    assert "label" not in before
    command.upgrade(config, "head")
    with engine.connect() as connection:
        after = dict(
            connection.exec_driver_sql(f"SELECT * FROM {schema}.annotations").mappings().one()
        )
    assert after.pop("label") is None
    assert after == before
    label_column = next(
        c for c in inspect(engine).get_columns("annotations", schema=schema) if c["name"] == "label"
    )
    assert label_column["nullable"]
    snapshot = store.load_snapshot(ids["recording"])
    assert snapshot.annotations[0].label is None
    assert snapshot.annotations[0].id == ids["annotation"]
