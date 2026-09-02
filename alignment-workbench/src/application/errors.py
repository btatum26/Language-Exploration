"""Stable exceptions exposed by the application persistence boundary."""


class PersistenceError(RuntimeError):
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
