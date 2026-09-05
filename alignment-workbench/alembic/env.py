from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, make_url, pool

from alembic import context
from application.ssh_tunnel import SshTunnel, SshTunnelConfig
from persistence.sqlalchemy import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

project_root = Path(__file__).resolve().parents[1]
load_dotenv(project_root / ".env", override=False)
target_metadata = Base.metadata


def _target_schema() -> str:
    value = config.attributes.get("snapshot_schema", "registry_align")
    if not isinstance(value, str) or not value.replace("_", "").isalnum():
        raise RuntimeError(
            "snapshot migration schema must contain only letters, digits, underscores"
        )
    return value


def _version_table() -> str:
    schema = _target_schema()
    if schema == "registry_align":
        return "registry_align_snapshot_alembic_version"
    return f"{schema}_alembic_version"


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    del name, type_, reflected, compare_to
    table = getattr(object_, "table", object_)
    if getattr(table, "name", None) == _version_table():
        return False
    return getattr(table, "schema", None) in {None, _target_schema()}


def _database_url() -> str:
    url = os.getenv("REGISTRY_ALIGN_DATABASE_ADMIN_URL") or os.getenv("REGISTRY_ALIGN_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "REGISTRY_ALIGN_DATABASE_ADMIN_URL or REGISTRY_ALIGN_DATABASE_URL is required"
        )
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("snapshot migrations require the postgresql+psycopg dialect")
    return (
        make_url(url)
        .update_query_dict({"options": "-c search_path=public"})
        .render_as_string(hide_password=False)
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=_include_object,
        version_table=_version_table(),
        version_table_schema="public",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = _database_url()
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url
    manage_tunnel = config.attributes.get("manage_ssh_tunnel", True)
    tunnel = (
        SshTunnel(SshTunnelConfig.from_mapping(database_url, os.environ)) if manage_tunnel else None
    )
    if tunnel is not None:
        tunnel.start()
    try:
        connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
        try:
            with connectable.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    include_schemas=True,
                    include_object=_include_object,
                    version_table=_version_table(),
                    version_table_schema="public",
                )
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            connectable.dispose()
    finally:
        if tunnel is not None:
            tunnel.stop()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
