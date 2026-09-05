"""Short-lived session boundaries and the application-facing SQLAlchemy facade."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, TypeVar
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session, sessionmaker

from application.errors import (
    DatabaseUnavailableError,
    PersistenceError,
    PersistenceIntegrityError,
)
from application.read_models import (
    AnnotationLibraryListItem,
    RecordingCatalogRecord,
    RecordingRevisionSummary,
    RecordingWorkspace,
)
from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    CreateRecordingRequest,
    Library,
    LibraryVersion,
    SaveRecordingSnapshotRequest,
    Speaker,
)
from persistence.sqlalchemy.audio_asset_repository import AudioAssetRepository
from persistence.sqlalchemy.engine import build_engine
from persistence.sqlalchemy.library_repository import LibraryRepository
from persistence.sqlalchemy.recording_repository import RecordingRepository
from persistence.sqlalchemy.session import build_session_factory
from persistence.sqlalchemy.speaker_repository import SpeakerRepository

T = TypeVar("T")


class SqlAlchemyPersistence:
    """One-method-per-transaction facade implementing the public store protocols."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        owned_engine: Engine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._owned_engine = owned_engine
        self._closed = False

    def __enter__(self) -> SqlAlchemyPersistence:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned_engine is not None:
            self._owned_engine.dispose()
            self._owned_engine = None

    def register_audio_asset(self, audio_asset: AudioAsset) -> AudioAsset:
        return self._run(lambda session: AudioAssetRepository(session).register(audio_asset))

    def get_audio_asset(self, audio_asset_id: UUID) -> AudioAsset:
        return self._run(lambda session: AudioAssetRepository(session).get(audio_asset_id))

    def create_speaker(self, speaker: Speaker) -> Speaker:
        return self._run(lambda session: SpeakerRepository(session).create(speaker))

    def get_speaker(self, speaker_id: UUID) -> Speaker:
        return self._run(lambda session: SpeakerRepository(session).get(speaker_id))

    def list_speakers(self) -> tuple[Speaker, ...]:
        return self._run(lambda session: SpeakerRepository(session).list())

    def create_library(self, library: Library) -> Library:
        return self._run(lambda session: LibraryRepository(session).create(library))

    def get_library(self, library_id: UUID) -> Library:
        return self._run(lambda session: LibraryRepository(session).get(library_id))

    def list_libraries(self) -> tuple[Library, ...]:
        return self._run(lambda session: LibraryRepository(session).list())

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        return self._run(lambda session: LibraryRepository(session).list_summaries())

    def publish_library_version(self, version: LibraryVersion) -> LibraryVersion:
        return self._run(lambda session: LibraryRepository(session).publish_version(version))

    def get_library_version(self, library_version_id: UUID) -> LibraryVersion:
        return self._run(lambda session: LibraryRepository(session).get_version(library_version_id))

    def list_library_versions(self, library_id: UUID) -> tuple[LibraryVersion, ...]:
        return self._run(lambda session: LibraryRepository(session).list_versions(library_id))

    def list_recordings(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[RecordingCatalogRecord, ...]:
        return self._run(
            lambda session: RecordingRepository(session).list(limit=limit, offset=offset)
        )

    def load_snapshot(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> AnnotatedRecordingSnapshot:
        return self._run(
            lambda session: RecordingRepository(session).load_snapshot(recording_id, revision_id)
        )

    def load_workspace(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> RecordingWorkspace:
        return self._run(
            lambda session: RecordingRepository(session).load_workspace(recording_id, revision_id)
        )

    def create_recording(self, request: CreateRecordingRequest) -> AnnotatedRecordingSnapshot:
        return self._run(lambda session: RecordingRepository(session).create(request))

    def save_snapshot(self, request: SaveRecordingSnapshotRequest) -> AnnotatedRecordingSnapshot:
        return self._run(lambda session: RecordingRepository(session).save(request))

    def list_recording_revisions(self, recording_id: UUID) -> tuple[RecordingRevisionSummary, ...]:
        return self._run(lambda session: RecordingRepository(session).list_revisions(recording_id))

    def _run(self, operation: Callable[[Session], T]) -> T:
        if self._closed:
            raise PersistenceError("persistence store is closed")
        try:
            with self._session_factory() as session:
                with session.begin():
                    return operation(session)
        except PersistenceError:
            raise
        except (OperationalError, InterfaceError) as exc:
            raise DatabaseUnavailableError("PostgreSQL is unavailable") from exc
        except IntegrityError as exc:
            raise PersistenceIntegrityError("PostgreSQL rejected the write") from exc
        except DBAPIError as exc:
            if _is_connection_failure(exc):
                raise DatabaseUnavailableError("PostgreSQL is unavailable") from exc
            raise PersistenceIntegrityError(
                "PostgreSQL rejected the persistence operation"
            ) from exc
        except (SQLAlchemyError, ValidationError) as exc:
            raise PersistenceIntegrityError("persistence operation failed") from exc


def create_persistence(database_url: str, **engine_options: Any) -> SqlAlchemyPersistence:
    """Create an owned persistence facade from configuration values only."""

    engine = build_engine(database_url, **engine_options)
    return SqlAlchemyPersistence(build_session_factory(engine), owned_engine=engine)


def _is_connection_failure(error: DBAPIError) -> bool:
    if error.connection_invalidated:
        return True
    sqlstate = getattr(error.orig, "sqlstate", None)
    return isinstance(sqlstate, str) and sqlstate.startswith("08")
