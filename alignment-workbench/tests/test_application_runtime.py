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


class LifecyclePersistence:
    def __init__(self, *, fail_startup: bool = False) -> None:
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
        self.close_calls += 1
        self.closed = True


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
    persistence = LifecyclePersistence()
    create_calls = 0

    def create(_database_url: str) -> LifecyclePersistence:
        nonlocal create_calls
        create_calls += 1
        return persistence

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
    assert create_calls == 1
    assert (tmp_path / "audio" / "assets").is_dir()
    assert (tmp_path / "recovery" / "pending").is_dir()

    application.shutdown()
    application.shutdown()

    assert not application.started
    assert persistence.close_calls == 1


def test_startup_failure_is_sanitized_and_disposes_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = LifecyclePersistence(fail_startup=True)
    monkeypatch.setattr(runtime_module, "create_persistence", lambda _url: persistence)
    application = WorkbenchApplication(settings(tmp_path))

    with pytest.raises(WorkbenchStartupError) as failure:
        application.start()

    assert str(failure.value) == "could not connect to the configured PostgreSQL database"
    assert "top-secret" not in str(failure.value)
    assert not application.started
    assert persistence.close_calls == 1


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
