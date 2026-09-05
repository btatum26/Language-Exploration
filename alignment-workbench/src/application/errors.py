"""Stable exceptions exposed by the workbench application boundary."""


class WorkbenchError(RuntimeError):
    """Base class for expected application failures."""


class PersistenceError(WorkbenchError):
    """Base class for expected persistence failures."""


class AudioAssetNotFoundError(PersistenceError):
    """The requested audio asset does not exist."""


class SpeakerNotFoundError(PersistenceError):
    """The requested speaker does not exist."""


class LibraryNotFoundError(PersistenceError):
    """The requested annotation library does not exist."""


class RecordingNotFoundError(PersistenceError):
    """The requested recording does not exist."""


class RevisionNotFoundError(PersistenceError):
    """The requested recording revision does not exist."""


class LibraryVersionNotFoundError(PersistenceError):
    """A requested or pinned library version does not exist."""


class ConcurrentRevisionError(PersistenceError):
    """A save was based on a recording head that is no longer current."""


class DatabaseUnavailableError(PersistenceError):
    """PostgreSQL could not be reached or the connection was lost."""


class PersistenceIntegrityError(PersistenceError):
    """Persisted data or a write violated the persistence contract."""


class AnnotationNotFoundError(WorkbenchError):
    """The requested annotation does not exist in the edit session."""


class DuplicateAnnotationError(WorkbenchError):
    """An annotation ID is already present in the edit session."""


class ConceptNotFoundError(WorkbenchError):
    """A concept does not exist in the referenced pinned library version."""


class ConceptNotPinnedError(WorkbenchError):
    """A concept references a library version that is not pinned."""


class GeometryNotAllowedError(WorkbenchError):
    """An annotation geometry is not allowed by its library entry."""


class GeometryOutOfBoundsError(WorkbenchError):
    """Annotation geometry exceeds the immutable audio bounds."""


class InvalidAnnotationAttributesError(WorkbenchError):
    """Annotation attributes fail the selected entry's JSON Schema."""


class LibraryVersionInUseError(WorkbenchError):
    """A pinned library version is still referenced by annotations."""


class AudioUnavailableError(WorkbenchError):
    """Audio cannot be found or resolved through the configured storage root."""


class AudioIntegrityError(WorkbenchError):
    """Audio bytes or metadata violate the immutable storage contract."""


class UnsupportedAudioError(WorkbenchError):
    """The audio format is not supported by the local storage implementation."""


class InvalidRecoveryEnvelopeError(WorkbenchError):
    """A durable recovery envelope is malformed or incompatible."""


class RecoveryStorageError(WorkbenchError):
    """The recovery outbox could not durably store or move an operation."""


class PendingRecoveryOperationError(WorkbenchError):
    """An operation must be resolved through RecoveryHandler before discarding state."""


class SessionClosedError(WorkbenchError):
    """An operation was attempted on a closed recording edit session."""


class UnsavedChangesError(WorkbenchError):
    """An edit session cannot close until changes are saved or explicitly discarded."""


class WorkbenchConfigurationError(WorkbenchError):
    """Application settings are missing or invalid."""


class WorkbenchStartupError(WorkbenchError):
    """Configured application dependencies could not be initialized or validated."""
