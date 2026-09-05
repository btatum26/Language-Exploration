"""Immutable application and persistence read projections."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    LibraryVersion,
    RevisionMetadata,
    Speaker,
)


@dataclass(frozen=True, slots=True)
class RecordingCatalogRecord:
    """Persistence projection used to assemble a public recording list item."""

    recording_id: UUID
    head_revision_id: UUID
    name: str
    speaker_display_name: str | None
    audio_asset: AudioAsset
    revision_number: int
    revised_at: datetime


class AudioAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class RecordingListItem:
    recording_id: UUID
    display_name: str
    speaker_display_name: str | None
    duration_seconds: float
    current_revision_id: UUID
    revision_number: int
    modified_at: datetime
    audio_status: AudioAvailability


@dataclass(frozen=True, slots=True)
class AnnotationLibraryListItem:
    library_id: UUID
    namespace: str
    name: str
    description: str | None
    latest_version_id: UUID | None
    latest_version_label: str | None
    latest_version_created_at: datetime | None
    entry_count: int


@dataclass(frozen=True, slots=True)
class RecordingRevisionSummary:
    metadata: RevisionMetadata
    annotation_count: int


@dataclass(frozen=True, slots=True)
class RecordingWorkspace:
    snapshot: AnnotatedRecordingSnapshot
    default_speaker: Speaker | None
    library_versions: tuple[LibraryVersion, ...]
