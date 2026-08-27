from __future__ import annotations

import socket
import subprocess
from typing import Any

import pytest
from sqlalchemy import create_mock_engine

from registry_align.config import SshTunnelConfig
from registry_align.errors import DatabaseError
from registry_align.storage.database import redact_secrets
from registry_align.storage.schema import alembic_config
from registry_align.storage.tables import metadata
from registry_align.storage.tunnel import SshTunnel


def test_metadata_contains_only_postgresql_tables() -> None:
    assert set(metadata.tables) == {
        "recordings",
        "audio_assets",
        "transcript_versions",
        "recording_versions",
        "alignment_results",
        "segments",
        "segment_revisions",
        "runs",
        "run_items",
        "issues",
        "speakers",
    }


def test_packaged_alembic_history_has_remote_audio_head() -> None:
    from alembic.script import ScriptDirectory

    assert ScriptDirectory.from_config(alembic_config()).get_current_head() == "20260826_0005"


def test_postgresql_schema_compiles() -> None:
    statements: list[str] = []
    engine = create_mock_engine(
        "postgresql+psycopg://",
        lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))),
    )

    metadata.create_all(engine)

    assert any("CREATE TABLE recordings" in statement for statement in statements)
    assert any("CREATE TABLE alignment_results" in statement for statement in statements)


def test_redacts_passwords_from_database_errors() -> None:
    source = "failed postgresql+psycopg://registry_align_app:very-secret@127.0.0.1/db"

    result = redact_secrets(source)

    assert "very-secret" not in result
    assert "registry_align_app:***@" in result


def test_tunnel_rejects_an_occupied_port() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        with pytest.raises(DatabaseError, match="already in use"):
            with SshTunnel(SshTunnelConfig(local_port=port)):
                pass
    finally:
        listener.close()


def test_tunnel_uses_ssh_alias_and_stops_process(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    class Process:
        stderr = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            commands.append(["terminate"])

        def wait(self, timeout: int) -> int:
            del timeout
            return 0

        def kill(self) -> None:
            raise AssertionError("kill should not be needed")

    def popen(command: list[str], **kwargs: Any) -> Process:
        del kwargs
        commands.append(command)
        return Process()

    states = iter((False, True))
    monkeypatch.setattr("registry_align.storage.tunnel.port_is_open", lambda *args: next(states))
    monkeypatch.setattr(subprocess, "Popen", popen)

    with SshTunnel(SshTunnelConfig(host="registry-db", local_port=5433)):
        pass

    assert commands[0] == ["ssh", "-N", "registry-db"]
    assert commands[-1] == ["terminate"]
