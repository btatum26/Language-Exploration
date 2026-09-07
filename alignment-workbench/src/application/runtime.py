"""Validated settings and the process-level workbench lifecycle."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from application.audio_storage import LocalAudioStorage
from application.contracts import CreateRecordingCommand
from application.edit_session import RecordingEditSession
from application.errors import (
    DatabaseUnavailableError,
    PersistenceError,
    WorkbenchConfigurationError,
    WorkbenchError,
    WorkbenchStartupError,
)
from application.read_models import AnnotationLibraryListItem, RecordingListItem
from application.recovery import FileRecoveryOutbox
from application.ssh_tunnel import SshTunnel, SshTunnelConfig
from application.workbench import WorkbenchAPI, create_workbench
from persistence.sqlalchemy import SqlAlchemyPersistence, create_persistence

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class WorkbenchSettings:
    """Non-secret paths plus a redacted-at-repr PostgreSQL connection URL."""

    database_url: str = field(repr=False)
    audio_root: Path
    recovery_root: Path
    ssh_alias: str = "registry-db"
    ssh_executable: str = "ssh"
    ssh_startup_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        try:
            url = make_url(self.database_url)
        except Exception as exc:
            raise WorkbenchConfigurationError(
                "REGISTRY_ALIGN_DATABASE_URL must be a valid PostgreSQL URL"
            ) from exc

        if url.drivername != "postgresql+psycopg":
            raise WorkbenchConfigurationError(
                "REGISTRY_ALIGN_DATABASE_URL must use postgresql+psycopg"
            )
        if not url.database:
            raise WorkbenchConfigurationError(
                "REGISTRY_ALIGN_DATABASE_URL must name a PostgreSQL database"
            )

        tunnel_config = SshTunnelConfig.from_mapping(
            self.database_url,
            {
                "ALIGNMENT_WORKBENCH_SSH_ALIAS": self.ssh_alias,
                "ALIGNMENT_WORKBENCH_SSH_EXECUTABLE": self.ssh_executable,
                "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS": str(
                    self.ssh_startup_timeout_seconds
                ),
            },
        )
        object.__setattr__(self, "ssh_alias", tunnel_config.ssh_alias)
        object.__setattr__(self, "ssh_executable", tunnel_config.executable)
        object.__setattr__(
            self,
            "ssh_startup_timeout_seconds",
            tunnel_config.startup_timeout_seconds,
        )
        object.__setattr__(self, "audio_root", self.audio_root.resolve())
        object.__setattr__(self, "recovery_root", self.recovery_root.resolve())

    @property
    def ssh_tunnel_config(self) -> SshTunnelConfig:
        return SshTunnelConfig.from_mapping(
            self.database_url,
            {
                "ALIGNMENT_WORKBENCH_SSH_ALIAS": self.ssh_alias,
                "ALIGNMENT_WORKBENCH_SSH_EXECUTABLE": self.ssh_executable,
                "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS": str(
                    self.ssh_startup_timeout_seconds
                ),
            },
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> WorkbenchSettings:
        database_url = (values.get("REGISTRY_ALIGN_DATABASE_URL") or "").strip()
        if not database_url:
            raise WorkbenchConfigurationError("REGISTRY_ALIGN_DATABASE_URL is required")
        audio_root_value = (values.get("ALIGNMENT_WORKBENCH_AUDIO_ROOT") or "").strip()
        if not audio_root_value:
            raise WorkbenchConfigurationError("ALIGNMENT_WORKBENCH_AUDIO_ROOT is required")
        audio_root = Path(audio_root_value)
        recovery_root_value = (values.get("ALIGNMENT_WORKBENCH_RECOVERY_ROOT") or "").strip()
        recovery_root = (
            Path(recovery_root_value) if recovery_root_value else audio_root / "recovery"
        )
        tunnel_config = SshTunnelConfig.from_mapping(database_url, values)
        return cls(
            database_url=database_url,
            audio_root=audio_root,
            recovery_root=recovery_root,
            ssh_alias=tunnel_config.ssh_alias,
            ssh_executable=tunnel_config.executable,
            ssh_startup_timeout_seconds=tunnel_config.startup_timeout_seconds,
        )

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> WorkbenchSettings:
        selected_env_file = env_file if env_file is not None else _PROJECT_ROOT / ".env"
        try:
            file_values = dotenv_values(selected_env_file) if selected_env_file.is_file() else {}
        except OSError as exc:
            raise WorkbenchConfigurationError(
                f"could not read application environment file {selected_env_file}"
            ) from exc
        values: dict[str, str | None] = dict(file_values)
        values.update(os.environ)
        return cls.from_mapping(values)


class WorkbenchApplication:
    """Own configured infrastructure and one started public API instance."""

    def __init__(self, settings: WorkbenchSettings) -> None:
        self._settings = settings
        self._tunnel: SshTunnel | None = None
        self._persistence: SqlAlchemyPersistence | None = None
        self._api: WorkbenchAPI | None = None

    @property
    def settings(self) -> WorkbenchSettings:
        return self._settings

    @property
    def started(self) -> bool:
        return self._api is not None

    @property
    def api(self) -> WorkbenchAPI:
        if self._api is None:
            raise WorkbenchStartupError("workbench application is not started")
        return self._api

    def start(self) -> WorkbenchApplication:
        if self.started:
            return self
        tunnel: SshTunnel | None = None
        persistence: SqlAlchemyPersistence | None = None
        try:
            tunnel = SshTunnel(self.settings.ssh_tunnel_config)
            tunnel.start()
            persistence = create_persistence(self.settings.database_url)
            audio_storage = LocalAudioStorage(self.settings.audio_root)
            recovery_outbox = FileRecoveryOutbox(self.settings.recovery_root)
            api = create_workbench(
                persistence=persistence,
                audio_storage=audio_storage,
                recovery_outbox=recovery_outbox,
            )
            api.list_recordings(limit=1)
            api.list_annotation_libraries()
            try:
                api.ensure_core_library()
            except PersistenceError as exc:
                raise WorkbenchStartupError(
                    "Core library initialization failed: could not read or publish core@0.1"
                ) from exc
        except DatabaseUnavailableError as exc:
            self._close_infrastructure(persistence, tunnel)
            raise WorkbenchStartupError(
                "could not connect to the configured PostgreSQL database"
            ) from exc
        except PersistenceError as exc:
            self._close_infrastructure(persistence, tunnel)
            raise WorkbenchStartupError(
                "configured PostgreSQL schema is missing or incompatible"
            ) from exc
        except WorkbenchError as exc:
            self._close_infrastructure(persistence, tunnel)
            raise WorkbenchStartupError(str(exc)) from exc
        except Exception:
            self._close_infrastructure(persistence, tunnel)
            raise

        self._tunnel = tunnel
        self._persistence = persistence
        self._api = api
        return self

    def shutdown(self) -> None:
        tunnel = self._tunnel
        persistence = self._persistence
        self._api = None
        self._persistence = None
        self._tunnel = None
        self._close_infrastructure(persistence, tunnel)

    @staticmethod
    def _close_infrastructure(
        persistence: SqlAlchemyPersistence | None,
        tunnel: SshTunnel | None,
    ) -> None:
        try:
            if persistence is not None:
                persistence.close()
        finally:
            if tunnel is not None:
                tunnel.stop()

    def __enter__(self) -> WorkbenchApplication:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()

    def list_recordings(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]:
        return self.api.list_recordings(limit=limit, offset=offset)

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        return self.api.list_annotation_libraries()

    def import_recording(self, command: CreateRecordingCommand) -> RecordingEditSession:
        return self.api.import_recording(command)

    def open_recording(self, recording_id: UUID) -> RecordingEditSession:
        return self.api.open_recording(recording_id)
