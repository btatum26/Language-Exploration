from __future__ import annotations

from pathlib import Path

import pytest

import application.runtime as runtime_module
from application import (
    DatabaseUnavailableError,
    WorkbenchApplication,
    WorkbenchConfigurationError,
    WorkbenchSettings,
    WorkbenchStartupError,
)
from application.read_models import AnnotationLibraryListItem, RecordingCatalogRecord
from tests.test_application_api import MemoryPersistenceStore


class LifecyclePersistence(MemoryPersistenceStore):
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        fail_startup: bool = False,
    ) -> None:
        super().__init__()
        self.events = events if events is not None else []
        self.fail_startup = fail_startup
        self.close_calls = 0
        self.closed = False

    def list_recordings(self, *, limit: int, offset: int) -> tuple[RecordingCatalogRecord, ...]:
        del limit, offset
        if self.fail_startup:
            raise DatabaseUnavailableError("secret-bearing driver detail")
        if self.closed:
            raise RuntimeError("closed")
        return ()

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        if self.closed:
            raise RuntimeError("closed")
        return ()

    def close(self) -> None:
        self.events.append("persistence-close")
        self.close_calls += 1
        self.closed = True


class LifecycleTunnel:
    def __init__(
        self,
        events: list[str] | None = None,
        *,
        fail_startup: bool = False,
    ) -> None:
        self.events = events if events is not None else []
        self.fail_startup = fail_startup
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> LifecycleTunnel:
        self.start_calls += 1
        self.events.append("tunnel-start")
        if self.fail_startup:
            raise WorkbenchStartupError("SSH tunnel failed")
        return self

    def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("tunnel-stop")


def settings(tmp_path: Path) -> WorkbenchSettings:
    return WorkbenchSettings(
        database_url="postgresql+psycopg://runtime:top-secret@localhost/workbench",
        audio_root=tmp_path / "audio",
        recovery_root=tmp_path / "recovery",
    )


def test_application_startup_and_shutdown_are_explicit_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    tunnel = LifecycleTunnel(events)
    persistence = LifecyclePersistence(events)
    create_calls = 0

    def create(_database_url: str) -> LifecyclePersistence:
        nonlocal create_calls
        create_calls += 1
        events.append("persistence-create")
        return persistence

    monkeypatch.setattr(runtime_module, "SshTunnel", lambda _config: tunnel)
    monkeypatch.setattr(runtime_module, "create_persistence", create)
    application = WorkbenchApplication(settings(tmp_path))

    assert not application.started
    with pytest.raises(WorkbenchStartupError, match="not started"):
        _ = application.api

    assert application.start() is application
    assert application.start() is application
    assert application.started
    assert application.list_recordings() == ()
    assert application.list_annotation_libraries() == ()
    assert tunnel.start_calls == 1
    assert create_calls == 1
    assert events[:2] == ["tunnel-start", "persistence-create"]
    assert (tmp_path / "audio" / "assets").is_dir()
    assert (tmp_path / "recovery" / "pending").is_dir()

    application.shutdown()
    application.shutdown()

    assert not application.started
    assert persistence.close_calls == 1
    assert tunnel.stop_calls == 1
    assert events[-2:] == ["persistence-close", "tunnel-stop"]


def test_startup_failure_is_sanitized_and_disposes_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tunnel = LifecycleTunnel()
    persistence = LifecyclePersistence(fail_startup=True)
    monkeypatch.setattr(runtime_module, "SshTunnel", lambda _config: tunnel)
    monkeypatch.setattr(runtime_module, "create_persistence", lambda _url: persistence)
    application = WorkbenchApplication(settings(tmp_path))

    with pytest.raises(WorkbenchStartupError) as failure:
        application.start()

    assert str(failure.value) == "could not connect to the configured PostgreSQL database"
    assert "top-secret" not in str(failure.value)
    assert not application.started
    assert persistence.close_calls == 1
    assert tunnel.stop_calls == 1


def test_tunnel_failure_prevents_persistence_creation_and_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tunnel = LifecycleTunnel(fail_startup=True)
    persistence_created = False

    def create(_database_url: str) -> LifecyclePersistence:
        nonlocal persistence_created
        persistence_created = True
        return LifecyclePersistence()

    monkeypatch.setattr(runtime_module, "SshTunnel", lambda _config: tunnel)
    monkeypatch.setattr(runtime_module, "create_persistence", create)

    with pytest.raises(WorkbenchStartupError, match="SSH tunnel failed"):
        WorkbenchApplication(settings(tmp_path)).start()

    assert not persistence_created
    assert tunnel.stop_calls == 1


def test_settings_reject_missing_or_non_postgresql_configuration(tmp_path: Path) -> None:
    with pytest.raises(WorkbenchConfigurationError, match="DATABASE_URL is required"):
        WorkbenchSettings.from_mapping({"ALIGNMENT_WORKBENCH_AUDIO_ROOT": str(tmp_path / "audio")})
    with pytest.raises(WorkbenchConfigurationError, match=r"postgresql\+psycopg"):
        WorkbenchSettings.from_mapping(
            {
                "REGISTRY_ALIGN_DATABASE_URL": "sqlite:///workbench.db",
                "ALIGNMENT_WORKBENCH_AUDIO_ROOT": str(tmp_path / "audio"),
            }
        )

    configured = settings(tmp_path)
    assert "top-secret" not in repr(configured)
    assert configured.ssh_tunnel_config.local_host == "localhost"
    assert configured.ssh_tunnel_config.local_port == 5432


def test_settings_read_optional_ssh_tunnel_overrides(tmp_path: Path) -> None:
    configured = WorkbenchSettings.from_mapping(
        {
            "REGISTRY_ALIGN_DATABASE_URL": (
                "postgresql+psycopg://runtime:secret@127.0.0.1:5433/workbench"
            ),
            "ALIGNMENT_WORKBENCH_AUDIO_ROOT": str(tmp_path / "audio"),
            "ALIGNMENT_WORKBENCH_SSH_ALIAS": "custom-registry-db",
            "ALIGNMENT_WORKBENCH_SSH_EXECUTABLE": "custom-ssh",
            "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS": "8.5",
        }
    )

    tunnel = configured.ssh_tunnel_config
    assert tunnel.ssh_alias == "custom-registry-db"
    assert tunnel.executable == "custom-ssh"
    assert tunnel.local_host == "127.0.0.1"
    assert tunnel.local_port == 5433
    assert tunnel.startup_timeout_seconds == 8.5

    with pytest.raises(WorkbenchConfigurationError, match="must be positive"):
        WorkbenchSettings.from_mapping(
            {
                "REGISTRY_ALIGN_DATABASE_URL": (
                    "postgresql+psycopg://runtime:secret@127.0.0.1:5433/workbench"
                ),
                "ALIGNMENT_WORKBENCH_AUDIO_ROOT": str(tmp_path / "audio"),
                "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS": "nan",
            }
        )
