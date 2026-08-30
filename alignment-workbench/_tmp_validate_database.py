from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
TEST_SCHEMA = f"ra_awb_{uuid4().hex[:8]}_test"


def port_is_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5433), timeout=1):
            return True
    except OSError:
        return False


def alembic_config(schema: str = "registry_align") -> Config:
    config = Config(ROOT / "alembic.ini")
    config.attributes["snapshot_schema"] = schema
    return config


def drop_test_objects(owner_engine: object) -> None:
    with owner_engine.begin() as connection:  # type: ignore[attr-defined]
        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        connection.exec_driver_sql(
            f'DROP TABLE IF EXISTS public."{TEST_SCHEMA}_alembic_version"'
        )


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    owner_url = os.environ["REGISTRY_ALIGN_DATABASE_ADMIN_URL"]
    runtime_url = os.environ["REGISTRY_ALIGN_DATABASE_URL"]
    tunnel: subprocess.Popen[bytes] | None = None
    if not port_is_open():
        tunnel = subprocess.Popen(
            ["ssh", "-N", "registry-db"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(30):
            if port_is_open():
                break
            time.sleep(1)
        else:
            raise RuntimeError("SSH tunnel did not become available on 127.0.0.1:5433")

    owner_engine = create_engine(owner_url, pool_pre_ping=True)
    try:
        command.upgrade(alembic_config(), "head")

        grant_sql = (ROOT / "sql" / "configure_runtime_role.sql").read_text(encoding="utf-8")
        grant_sql = "\n".join(
            line for line in grant_sql.splitlines() if not line.lstrip().startswith("--")
        )
        with owner_engine.begin() as connection:
            for statement in grant_sql.split(";"):
                statement = statement.strip()
                if statement and statement.upper() not in {"BEGIN", "COMMIT"}:
                    connection.exec_driver_sql(statement)

        test_environment = os.environ.copy()
        test_environment["TEST_DATABASE_URL"] = owner_url
        test_environment["TEST_DATABASE_SCHEMA"] = TEST_SCHEMA
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "tests/database/test_snapshot_postgresql.py",
            ],
            cwd=ROOT,
            env=test_environment,
            check=True,
        )
        drop_test_objects(owner_engine)

        runtime_environment = os.environ.copy()
        runtime_environment["TEST_RUNTIME_DATABASE_URL"] = runtime_url
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "tests/database/test_runtime_role_postgresql.py",
            ],
            cwd=ROOT,
            env=runtime_environment,
            check=True,
        )
        command.check(alembic_config())

        with owner_engine.connect() as connection:
            live_revision = connection.scalar(
                text(
                    "SELECT version_num "
                    "FROM public.registry_align_snapshot_alembic_version"
                )
            )
        print(f"live_revision={live_revision}")
        print("runtime_role_verified=true")
    finally:
        drop_test_objects(owner_engine)
        owner_engine.dispose()
        if tunnel is not None:
            tunnel.terminate()
            tunnel.wait(timeout=10)


if __name__ == "__main__":
    main()
