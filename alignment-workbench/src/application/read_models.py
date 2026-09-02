"""Immutable read projections returned by persistence interfaces."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from models import AnnotatedRecordingSnapshot, LibraryVersion, RevisionMetadata, Speaker


@dataclass(frozen=True, slots=True)
class RecordingSummary:
    recording_id: UUID
    head_revision_id: UUID
    name: str
    language: str
    audio_storage_uri: str
    duration_seconds: float
    revision_number: int
    revised_at: datetime


@dataclass(frozen=True, slots=True)
class RecordingRevisionSummary:
    metadata: RevisionMetadata
    annotation_count: int


@dataclass(frozen=True, slots=True)
class RecordingWorkspace:
    snapshot: AnnotatedRecordingSnapshot
    default_speaker: Speaker | None
    library_versions: tuple[LibraryVersion, ...]
