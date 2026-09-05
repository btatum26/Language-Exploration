"""Use-case-oriented persistence interfaces with no SQLAlchemy types."""

from typing import Protocol
from uuid import UUID

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


class AudioAssetStore(Protocol):
    def register_audio_asset(self, audio_asset: AudioAsset) -> AudioAsset: ...

    def get_audio_asset(self, audio_asset_id: UUID) -> AudioAsset: ...


class SpeakerStore(Protocol):
    def create_speaker(self, speaker: Speaker) -> Speaker: ...

    def get_speaker(self, speaker_id: UUID) -> Speaker: ...

    def list_speakers(self) -> tuple[Speaker, ...]: ...


class LibraryStore(Protocol):
    def create_library(self, library: Library) -> Library: ...

    def get_library(self, library_id: UUID) -> Library: ...

    def list_libraries(self) -> tuple[Library, ...]: ...

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]: ...

    def publish_library_version(self, version: LibraryVersion) -> LibraryVersion: ...

    def get_library_version(self, library_version_id: UUID) -> LibraryVersion: ...

    def list_library_versions(self, library_id: UUID) -> tuple[LibraryVersion, ...]: ...


class RecordingStore(Protocol):
    def list_recordings(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[RecordingCatalogRecord, ...]: ...

    def load_snapshot(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> AnnotatedRecordingSnapshot: ...

    def load_workspace(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> RecordingWorkspace: ...

    def create_recording(self, request: CreateRecordingRequest) -> AnnotatedRecordingSnapshot: ...

    def save_snapshot(
        self, request: SaveRecordingSnapshotRequest
    ) -> AnnotatedRecordingSnapshot: ...

    def list_recording_revisions(
        self, recording_id: UUID
    ) -> tuple[RecordingRevisionSummary, ...]: ...


class PersistenceStore(AudioAssetStore, SpeakerStore, LibraryStore, RecordingStore, Protocol):
    """Combined convenience contract; focused protocols remain independently usable."""
