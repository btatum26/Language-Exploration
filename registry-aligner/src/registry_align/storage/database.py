"""PostgreSQL engine construction and safe connection reporting."""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from registry_align.config import DatabaseConfig, database_url
from registry_align.errors import DatabaseError
from registry_align.storage.tunnel import SshTunnel

URL_CREDENTIALS = re.compile(r"(postgresql(?:\+psycopg)?://[^:/\s]+:)([^@\s]+)(@)")


def redact_secrets(value: str) -> str:
    return URL_CREDENTIALS.sub(r"\1***\3", value)


def create_database_engine(url: str, config: DatabaseConfig) -> Engine:
    return create_engine(
        url,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": config.connect_timeout_seconds,
            "options": f"-c statement_timeout={config.statement_timeout_ms}",
        },
    )


class DatabaseRuntime(AbstractContextManager[Engine]):
    def __init__(self, config: DatabaseConfig, *, connection_url: str | None = None) -> None:
        self.config = config
        self.connection_url = connection_url
        self.tunnel = SshTunnel(config.ssh_tunnel)
        self.engine: Engine | None = None

    def __enter__(self) -> Engine:
        self.tunnel.__enter__()
        try:
            self.engine = create_database_engine(self.connection_url or database_url(), self.config)
            return self.engine
        except Exception:
            self.tunnel.stop()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.tunnel.stop()


def check_database(engine: Engine) -> dict[str, Any]:
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT current_user, current_database(), current_schema(), version(), "
                    "n.nspowner::regrole::text, "
                    "has_schema_privilege(current_user,current_schema(),'USAGE'), "
                    "has_schema_privilege(current_user,current_schema(),'CREATE'), "
                    "has_database_privilege(current_user,current_database(),'CREATE') "
                    "FROM pg_namespace n WHERE n.nspname=current_schema()"
                )
            ).one()
            relation = f"{row[2]}.runs"
            table_exists = connection.execute(
                text("SELECT to_regclass(:relation)"), {"relation": relation}
            ).scalar_one_or_none()
            table_access: dict[str, bool] | None = None
            if table_exists is not None:
                privileges = connection.execute(
                    text(
                        "SELECT has_table_privilege(current_user,:relation,'SELECT'), "
                        "has_table_privilege(current_user,:relation,'INSERT'), "
                        "has_table_privilege(current_user,:relation,'UPDATE'), "
                        "has_table_privilege(current_user,:relation,'DELETE')"
                    ),
                    {"relation": relation},
                ).one()
                table_access = {
                    "select": privileges[0],
                    "insert": privileges[1],
                    "update": privileges[2],
                    "delete": privileges[3],
                }
        return {
            "usable": True,
            "user": row[0],
            "database": row[1],
            "schema": row[2],
            "server": str(row[3]).split(",", 1)[0],
            "schema_owner": row[4],
            "schema_usage": row[5],
            "schema_create": row[6],
            "database_create": row[7],
            "runs_table_access": table_access,
        }
    except SQLAlchemyError as exc:
        raise DatabaseError(f"PostgreSQL connection failed: {redact_secrets(str(exc))}") from exc
