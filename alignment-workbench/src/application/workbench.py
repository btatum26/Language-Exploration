"""Composition root for the synchronous workbench application API."""

from __future__ import annotations

from application.contracts import AudioStorageHandler, RecoveryOutbox
from application.handlers import (
    LibraryHandler,
    RecordingHandler,
    RecoveryHandler,
    SpeakerHandler,
)
from application.persistence import PersistenceStore
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
