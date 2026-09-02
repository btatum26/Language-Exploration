"""Persistence-independent application contracts."""

from application.errors import (
    AudioAssetNotFoundError,
    ConcurrentRevisionError,
    DatabaseUnavailableError,
    LibraryNotFoundError,
    LibraryVersionNotFoundError,
    PersistenceError,
    PersistenceIntegrityError,
    RecordingNotFoundError,
    RevisionNotFoundError,
    SpeakerNotFoundError,
)
from application.persistence import (
    AudioAssetStore,
    LibraryStore,
    PersistenceStore,
    RecordingStore,
    SpeakerStore,
)
from application.read_models import (
    RecordingRevisionSummary,
    RecordingSummary,
    RecordingWorkspace,
)

__all__ = [
    "AudioAssetStore",
    "AudioAssetNotFoundError",
    "ConcurrentRevisionError",
    "DatabaseUnavailableError",
    "LibraryStore",
    "LibraryNotFoundError",
    "LibraryVersionNotFoundError",
    "PersistenceError",
    "PersistenceIntegrityError",
    "PersistenceStore",
    "RecordingNotFoundError",
    "RecordingRevisionSummary",
    "RecordingStore",
    "RecordingSummary",
    "RecordingWorkspace",
    "RevisionNotFoundError",
    "SpeakerStore",
    "SpeakerNotFoundError",
]
