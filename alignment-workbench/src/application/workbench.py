"""Composition root for the synchronous workbench application API."""

from __future__ import annotations

from uuid import UUID

from application.contracts import AudioStorageHandler, CreateRecordingCommand, RecoveryOutbox
from application.edit_session import RecordingEditSession
from application.handlers import (
    LibraryHandler,
    RecordingHandler,
    RecoveryHandler,
    SpeakerHandler,
)
from application.persistence import PersistenceStore
from application.read_models import AnnotationLibraryListItem, RecordingListItem
from application.validation import LibraryCatalog


class WorkbenchAPI:
    """Public entry point grouping the four application handlers."""

    def __init__(
        self,
        *,
        persistence: PersistenceStore,
        audio_storage: AudioStorageHandler,
        recovery_outbox: RecoveryOutbox,
    ) -> None:
        catalog = LibraryCatalog()
        libraries = LibraryHandler(persistence, catalog)
        self._recordings = RecordingHandler(
            persistence=persistence,
            audio_storage=audio_storage,
            recovery_outbox=recovery_outbox,
            libraries=libraries,
            library_catalog=catalog,
        )
        self._libraries = libraries
        self._speakers = SpeakerHandler(persistence)
        self._recovery = RecoveryHandler(persistence, recovery_outbox)

    @property
    def recordings(self) -> RecordingHandler:
        return self._recordings

    @property
    def libraries(self) -> LibraryHandler:
        return self._libraries

    @property
    def speakers(self) -> SpeakerHandler:
        return self._speakers

    @property
    def recovery(self) -> RecoveryHandler:
        return self._recovery

    def list_recordings(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]:
        return self._recordings.list(limit=limit, offset=offset)

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        return self._libraries.list_items()

    def import_recording(self, command: CreateRecordingCommand) -> RecordingEditSession:
        return self._recordings.create(command)

    def open_recording(self, recording_id: UUID) -> RecordingEditSession:
        return self._recordings.open(recording_id)


def create_workbench(
    *,
    persistence: PersistenceStore,
    audio_storage: AudioStorageHandler,
    recovery_outbox: RecoveryOutbox,
) -> WorkbenchAPI:
    return WorkbenchAPI(
        persistence=persistence,
        audio_storage=audio_storage,
        recovery_outbox=recovery_outbox,
    )
