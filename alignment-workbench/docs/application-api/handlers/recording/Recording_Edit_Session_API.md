# RecordingEditSession API

**Status:** Implemented

[Application API overview](../../Alignment_Workbench_Unified_Application_API.md)


`RecordingEditSession` is the primary API for working with one recording. It owns the editable recording metadata, annotations, pinned library versions, local undo history, resolved audio, and save coordination.

It must not expose a SQLAlchemy session or retain an open database transaction.

## State properties

```python
class RecordingEditSession(Protocol):
    @property
    def recording_id(self) -> UUID: ...

    @property
    def base_revision_id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def language(self) -> str: ...

    @property
    def default_speaker_ref(self) -> UUID | None: ...

    @property
    def audio(self) -> ResolvedAudio: ...

    @property
    def annotations(self) -> tuple[SignalAnnotation, ...]: ...

    @property
    def pinned_libraries(self) -> tuple[LibraryVersion, ...]: ...

    @property
    def dirty(self) -> bool: ...

    @property
    def sync_state(self) -> SyncState: ...

    @property
    def can_undo(self) -> bool: ...

    @property
    def can_redo(self) -> bool: ...
```

Returned tuples and domain objects are immutable. Callers modify state through session methods.

## Recording metadata

```python
def set_name(self, name: str) -> None: ...

def set_language(self, language: str) -> None: ...

def set_default_speaker(self, speaker_id: UUID | None) -> None: ...
```

An effective change marks the session dirty and creates one undoable edit. Setting a field to its existing value is a no-op.

## Annotation creation and editing

```python
def create_annotation(
    self,
    *,
    concept_ref: ConceptRef,
    geometry: Geometry,
    attributes: Mapping[str, object] | None = None,
    confidence: float | None = None,
    note: str | None = None,
    provenance_ref: str | None = None,
) -> SignalAnnotation: ...

def add_annotation(self, annotation: SignalAnnotation) -> None: ...

def add_annotations(
    self,
    annotations: Iterable[SignalAnnotation],
) -> tuple[SignalAnnotation, ...]: ...

def get_annotation(self, annotation_id: UUID) -> SignalAnnotation: ...

def replace_annotation(
    self,
    annotation_id: UUID,
    replacement: SignalAnnotation,
) -> SignalAnnotation: ...

def remove_annotation(self, annotation_id: UUID) -> SignalAnnotation: ...

def find_annotations(
    self,
    query: AnnotationQuery,
) -> tuple[SignalAnnotation, ...]: ...
```

Rules:

- `create_annotation` generates a new annotation UUID.
- `add_annotation` preserves the supplied UUID and is suitable for imports and external analysis results.
- `add_annotations` is atomic: either the entire batch is accepted or none of it is applied.
- Annotation IDs must be unique within the editable snapshot.
- Replacement preserves the target identity unless the caller explicitly supplies the same ID in the replacement.
- Annotation order is stable. Adding appends; replacement keeps the existing position; removal closes the position without changing the relative order of remaining annotations.
- Mutations validate only the changed annotation or batch plus required aggregate-local uniqueness checks.
- Annotation lookup uses a session-owned UUID index that is updated with each annotation mutation and retained with undo/redo state.

## Library use inside the session

```python
def list_available_concepts(self) -> tuple[LibraryEntry, ...]: ...

def resolve_concept(self, concept_ref: ConceptRef) -> LibraryEntry: ...

def pin_library_version(self, version: LibraryVersion) -> None: ...

def unpin_library_version(
    self,
    namespace: str,
    version: str,
) -> None: ...
```

The session builds an in-memory concept index and compiles one attribute validator per library-entry UUID. These caches are reused for annotation mutations and concept resolution, and are rebuilt only when the pinned library set changes.

Creating, adding, or replacing an annotation requires:

1. The referenced namespace and version are pinned.
2. The referenced entry exists in that exact version.
3. The annotation geometry is permitted by the entry.
4. The geometry lies within the recording audio frame count.
5. Any currently enabled library attribute policy accepts the attributes.

Unpinning fails while any annotation still references that library version. The caller must first remove or relabel those annotations.

## Undo and redo

```python
def undo(self) -> bool: ...

def redo(self) -> bool: ...

def clear_edit_history(self) -> None: ...
```

- Each public mutation is one undoable edit.
- `add_annotations` is one batch edit.
- Undo and redo never access PostgreSQL.
- A new mutation after undo clears the redo branch.
- Saving does not delete undo history, although the implementation may mark the current position as the saved checkpoint.

## Database revision history

```python
def list_revisions(self) -> tuple[RecordingRevisionSummary, ...]: ...

def load_revision(
    self,
    revision_id: UUID,
) -> AnnotatedRecordingSnapshot: ...

def restore_revision(self, revision_id: UUID) -> None: ...
```

`restore_revision` copies the historical revision's editable contents into the current session and marks the session dirty. It does not replace `base_revision_id` with the historical revision. The next save remains a child of the head on which the session was originally based.

## Reload and discard

```python
def reload(self) -> None: ...

def discard_unsaved_changes(self) -> None: ...
```

- `reload` fetches the current authoritative head, replaces the session contents, updates the annotation index, rebuilds library-derived caches only if the pins changed, clears local edit history, and sets `sync_state` to `SYNCED`.
- `discard_unsaved_changes` returns to the most recently captured local save checkpoint without performing database I/O.
- Neither method may silently discard a pending recovery operation. A pending or conflicted operation requires an explicit recovery decision.

## Save

```python
def save(
    self,
    *,
    author: str | None = None,
    message: str | None = None,
) -> SaveResult: ...
```

Saving:

1. Creates a stable new revision UUID before any I/O.
2. Builds a complete `SaveRecordingSnapshotRequest` from current session state.
3. Writes the recovery envelope atomically.
4. Attempts the idempotent persistence operation only when the session is synchronized.
5. Returns `Saved`, `Queued`, or `SaveConflict`.

On `Saved`:

- `base_revision_id` advances to the saved revision;
- the saved snapshot becomes the local checkpoint;
- `dirty` becomes false;
- `sync_state` becomes `SYNCED`;
- the recovery item is marked applied.

On `Queued`:

- the current snapshot is durable locally;
- `dirty` becomes false relative to that local checkpoint;
- `sync_state` becomes `PENDING`;
- PostgreSQL is not reported as updated.
- additional saves remain queued as children of the newest local revision, even if connectivity has returned;
- `RecoveryHandler.retry_all()` applies the dependent chain in parent order.

On `SaveConflict`:

- local contents remain unchanged;
- `sync_state` becomes `CONFLICT`;
- no automatic merge occurs.
- further saves raise `PendingRecoveryOperationError` until the conflict is archived and the session is reloaded.

## External analysis boundary

Signal analysis is deliberately outside this API layer.

An external GUI, CLI, or analysis library may use:

- `session.audio.local_path`;
- `session.audio.asset`;
- `session.annotations`;
- `session.pinned_libraries`.

It returns ordinary annotations and applies them through the session. The complete call flow is
shown in [Application API Usage](../../Usage.md#external-analysis).

The data-handler layer has no `AnnotationProducer`, analyzer registry, analysis request type, model runner, or analysis job scheduler. If analysis capabilities are later added to a GUI, they still submit normal domain annotations through `RecordingEditSession`.
