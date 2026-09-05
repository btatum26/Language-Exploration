# Shared Application Contracts

**Status:** Implemented

[Application API overview](../Alignment_Workbench_Unified_Application_API.md)

## Purpose

This document defines the unified Python API between tools such as the Alignment Workbench GUI or a CLI and the underlying data-handling system.

The API owns:

- creating, listing, opening, editing, and saving recordings;
- creating and publishing annotation libraries;
- resolving library concepts while editing annotations;
- immutable recording revision history;
- immutable annotation-library versions;
- speaker identities;
- local immutable audio storage and logical audio URI resolution;
- durable recovery saves when PostgreSQL is unavailable;
- translation between application models and the persistence layer.

The API does not perform signal analysis. Analysis belongs to the GUI, CLI, or another tool built on top of this layer. An external tool may read the opened recording and submit ordinary `SignalAnnotation` objects to the editing session.

The API is an internal synchronous Python API. It is not an HTTP or REST API. A future transport may wrap these interfaces without changing their semantics.

## Design principles

1. `WorkbenchApplication` is the configured process lifecycle; its started `WorkbenchAPI` is the public behavior entry point.
2. `RecordingEditSession` is the public interface for meaningful work on one recording.
3. SQLAlchemy rows, sessions, expressions, and database-specific types never cross the application boundary.
4. A recording edit session is in-memory state. It does not hold an open database transaction.
5. Audio is immutable and stored outside PostgreSQL. PostgreSQL stores its logical URI and metadata.
6. An annotation is part of a complete recording snapshot. There is no independent annotation persistence CRUD API.
7. Saving creates a new immutable recording revision. Existing revisions are never updated or deleted.
8. Published library versions are immutable. Corrections require publishing a new version.
9. Undo and redo are local editing history, not database revision history.
10. Recovery files are durable pending operations, not a second authoritative database.
11. There is no semantic relation, annotation graph, containment tree, or generic relation API.
12. Expected operational outcomes such as an offline save or stale-head conflict use typed results rather than raw infrastructure exceptions.

## Layering

```text
Workbench GUI / CLI / analysis tool
                |
                v
           WorkbenchAPI
      +---------+----------+----------+
      |         |          |          |
 Recordings  Libraries  Speakers   Recovery
      |
      v
RecordingEditSession
      |
      +------ PersistenceStore ------ PostgreSQL
      +------ AudioStorageHandler --- local immutable audio
      +------ RecoveryOutbox -------- local pending saves
```

Public callers use `WorkbenchAPI` and `RecordingEditSession`. The persistence, audio-storage, and recovery implementations are injected infrastructure dependencies.

`WorkbenchApplication` keeps configuration, startup, API construction, and shutdown distinct. `WorkbenchSettings.from_environment()` reads the project `.env` plus process overrides. Creating the application does no I/O; `start()` validates PostgreSQL and local roots; `shutdown()` is idempotent and disposes the owned connection pool.

The first GUI discovery surface is also available directly on both the lifecycle owner and API facade:

```python
list_recordings(limit=100, offset=0) -> tuple[RecordingListItem, ...]
list_annotation_libraries() -> tuple[AnnotationLibraryListItem, ...]
import_recording(command) -> RecordingEditSession
open_recording(recording_id) -> RecordingEditSession
```

The list-item dataclasses are frozen and contain only application/domain values. Recording availability combines authoritative PostgreSQL metadata with a cheap local-path resolution check; full hashing occurs on import, explicit verification, and open.

## Public handler map

```python
class WorkbenchAPI(Protocol):
    @property
    def recordings(self) -> RecordingHandler: ...

    @property
    def libraries(self) -> LibraryHandler: ...

    @property
    def speakers(self) -> SpeakerHandler: ...

    @property
    def recovery(self) -> RecoveryHandler: ...
```

The initial public surface contains four handlers:

| Handler | Responsibility |
| --- | --- |
| `RecordingHandler` | Recording catalog, creation, opening, and revision summaries |
| `LibraryHandler` | Library creation, immutable publication, and version lookup |
| `SpeakerHandler` | Speaker creation and lookup |
| `RecoveryHandler` | Pending saves, retries, conflicts, and archival |

`RecordingEditSession` is returned by the recording handler and contains annotation editing, pinned-library use, recording metadata editing, revision restoration, undo/redo, and saving.

`AudioStorageHandler` and `PersistenceStore` are internal ports. They are documented because implementations must honor their contracts, but they are not exposed from `WorkbenchAPI`.

## Shared application types

### Open recording state

```python
@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    asset: AudioAsset
    local_path: Path
```

The edit session exposes the resolved audio asset and path. Playback and analysis may read the path, but this API does not decode or play the audio.

### Synchronization state

```python
class SyncState(StrEnum):
    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"
```

- `SYNCED`: the most recently captured save is authoritative in PostgreSQL.
- `PENDING`: the most recently captured save is durable locally but not confirmed in PostgreSQL.
- `CONFLICT`: the pending state cannot be applied because the authoritative head has changed.

`dirty` and `sync_state` are separate. A session may be locally modified while an earlier save is pending.

### Save results

```python
@dataclass(frozen=True, slots=True)
class Saved:
    snapshot: AnnotatedRecordingSnapshot


@dataclass(frozen=True, slots=True)
class Queued:
    operation_id: UUID
    revision_id: UUID


@dataclass(frozen=True, slots=True)
class SaveConflict:
    operation_id: UUID
    expected_head_revision_id: UUID | None
    current_head_revision_id: UUID | None


SaveResult = Saved | Queued | SaveConflict
```

The application must not report a queued save as remotely committed.

### Recording creation command

```python
class CreateRecordingCommand(DomainModel):
    recording_id: UUID
    initial_revision_id: UUID
    source_audio_path: Path
    name: NonEmptyStr
    language: NonEmptyStr
    default_speaker_ref: UUID | None = None
    libraries: tuple[PinnedLibraryVersion, ...] = ()
    annotations: tuple[SignalAnnotation, ...] = ()
    author: str | None = None
    message: str | None = None
```

The recording and revision identifiers are created before the database attempt so creation and recovery are idempotent.

### Annotation query

```python
@dataclass(frozen=True, slots=True)
class AnnotationQuery:
    start_sample: int | None = None
    end_sample: int | None = None
    min_frequency_hz: float | None = None
    max_frequency_hz: float | None = None
    concept_refs: frozenset[ConceptRef] = frozenset()
    namespaces: frozenset[str] = frozenset()
    geometry_types: frozenset[GeometryType] = frozenset()
```

Unset fields do not filter. Time queries return annotations whose geometry intersects the requested window. Frequency fields apply only to frequency-bearing geometries.

## Versioning semantics

The system has three different histories:

| History | Meaning | Persistence |
| --- | --- | --- |
| Recording revisions | Explicit authoritative saves of complete recording state | PostgreSQL, immutable |
| Library versions | Explicit publications of immutable annotation definitions | PostgreSQL, immutable |
| Edit-session undo history | Unsaved user edits since opening or reloading | Memory only |

Recovery files are not a fourth canonical history. They protect operations that are not yet confirmed in PostgreSQL.

Saving an unchanged state may still create a revision because it represents explicit user intent. Restoring an older recording revision creates a new head revision; it never rewinds history.

## Validation boundaries

| Boundary | Required validation |
| --- | --- |
| External JSON or registry import | Full structural and semantic validation |
| Audio ingestion | File readability, hash, media metadata, and storage safety |
| Session mutation | Only affected annotations, concepts, geometry, bounds, and local uniqueness |
| Batch annotation addition | Validate complete batch atomically |
| Save construction | Complete snapshot consistency and required database-dependent checks |
| Persistence write | Referential integrity, pin hashes, concept resolution, permissions, and concurrency |
| Normal database load | Centralized trusted hydration; no repeated external-input parsing |
| Explicit audit | Full database-to-domain and audio verification |

The API must not validate every annotation on every mouse movement, selection, playback update, or property read.

## Error model

Expected infrastructure and concurrency outcomes use result objects. Invalid operations and corrupted state use typed exceptions.

The public application errors include:

```python
class WorkbenchError(RuntimeError): ...

class RecordingNotFoundError(WorkbenchError): ...
class RevisionNotFoundError(WorkbenchError): ...
class AnnotationNotFoundError(WorkbenchError): ...
class DuplicateAnnotationError(WorkbenchError): ...
class LibraryNotFoundError(WorkbenchError): ...
class LibraryVersionNotFoundError(WorkbenchError): ...
class ConceptNotFoundError(WorkbenchError): ...
class ConceptNotPinnedError(WorkbenchError): ...
class GeometryNotAllowedError(WorkbenchError): ...
class GeometryOutOfBoundsError(WorkbenchError): ...
class InvalidAnnotationAttributesError(WorkbenchError): ...
class LibraryVersionInUseError(WorkbenchError): ...
class AudioUnavailableError(WorkbenchError): ...
class AudioIntegrityError(WorkbenchError): ...
class UnsupportedAudioError(WorkbenchError): ...
class InvalidRecoveryEnvelopeError(WorkbenchError): ...
class RecoveryStorageError(WorkbenchError): ...
class PendingRecoveryOperationError(WorkbenchError): ...
class SessionClosedError(WorkbenchError): ...
class UnsavedChangesError(WorkbenchError): ...
class WorkbenchConfigurationError(WorkbenchError): ...
class WorkbenchStartupError(WorkbenchError): ...
```

Raw SQLAlchemy, psycopg, filesystem, decoder, and JSON parsing exceptions must not escape through the public handler API.

## Threading and lifecycle

The API is synchronous.

- GUI code runs blocking handler calls in worker threads.
- An edit session may be owned by one interaction thread at a time.
- A session does not share SQLAlchemy sessions between calls.
- In-memory annotation reads do not require database calls.
- Rendering, playback, and analysis may safely consume immutable snapshots supplied by the session.
- The session implementation should provide immutable snapshots to background consumers rather than sharing partially mutated internal collections.

## Explicit exclusions

The initial unified API does not include:

- signal-analysis algorithms;
- an `AnnotationProducer` abstraction;
- analyzer discovery or plugin registration;
- automatic annotation acceptance;
- audio playback or decoding controls;
- waveform or spectrogram rendering;
- GUI widgets or Qt types;
- HTTP endpoints;
- authentication or multi-user authorization;
- semantic annotation relations;
- generic graph behavior;
- automatic conflict merging;
- in-place audio editing;
- deletion of canonical revisions or published library versions.

These exclusions do not prevent external tools from reading a session and adding annotations through the stable API.
