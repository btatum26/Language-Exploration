# SQLAlchemy Persistence Layer

**Status:** Implemented
**Scope:** Model-facing PostgreSQL persistence boundary owned by `alignment-workbench`

## Boundary

The implemented flow is:

```text
Future GUI / CLI
    -> proposed application handlers and edit session
        -> application persistence protocols
            -> synchronous SQLAlchemy implementation
                -> PostgreSQL
```

The proposed application layer is not part of this implementation. It will own editing commands
and use-case coordination. This layer owns short-lived database transactions, exact snapshot
hydration, immutable revision insertion, and persistence error translation.

Application code imports `AudioAsset`, `Speaker`, `Library`, `LibraryVersion`, `LibraryEntry`,
`AnnotatedRecordingSnapshot`, and request models from `models.py`. It imports protocols, read
models, and exceptions from `application`. ORM rows stay inside `persistence.sqlalchemy`.

## Public interfaces

The focused protocols are:

- `AudioAssetStore`: register or retrieve immutable audio metadata.
- `SpeakerStore`: create, retrieve, and list speakers.
- `LibraryStore`: create and retrieve libraries; publish, retrieve, and list immutable versions
  with ordered entries.
- `RecordingStore`: list recordings, create an initial snapshot, load current or historical
  snapshots, load a workspace, list revision history, and append a snapshot revision.
- `PersistenceStore`: a convenience composition of the four focused protocols.

`RecordingCatalogRecord`, `AnnotationLibraryListItem`, `RecordingRevisionSummary`, and `RecordingWorkspace` are frozen read models. The recording handler converts the internal catalog record into a smaller `RecordingListItem` after checking local audio availability.
A workspace contains the exact snapshot, its resolved optional speaker, and complete pinned
`LibraryVersion` models in pin order. Every library version contains its entries in stored order.

## Domain models and ORM rows

Domain models define the public aggregate and validate command input. ORM rows represent the nine
installed tables and are internal implementation details. A snapshot is assembled centrally by
`mappers.py` and `recording_queries.py`; row objects are never returned. Hydration constructs
normal Pydantic domain models directly and does not serialize rows through JSON or use scattered
validation bypasses.

SQLAlchemy relationships retain `lazy="raise"`. Snapshot loading uses explicit joins and never
depends on implicit lazy loading.

## Transactions and connections

`SqlAlchemyPersistence` opens one synchronous session per public operation. Each operation runs in
one `session.begin()` boundary. The facade commits successful operations and rolls back failures;
repositories never commit. Sessions always close after the operation, including reads.

`create_persistence()` builds a conservative PostgreSQL engine with `pool_pre_ping=True`, a
five-connection default pool, two overflow connections, configurable pool and connection timeouts,
and the `alignment-workbench` application name. Credentials come from the caller-supplied URL.
The layer neither reads credentials from source nor starts an SSH tunnel.

## Read query strategy

Loading a snapshot uses three bounded queries:

1. Recording, selected or head revision, audio asset, and optional speaker.
2. Pinned library versions joined to their libraries in stored `position` order.
3. Annotations joined to their exact entries, versions, and libraries in stored `position` order.

Loading a workspace adds at most one query for every entry belonging to all pinned versions. It
uses three SELECT statements when no libraries are pinned and four when pins exist, regardless of
annotation or entry count. It avoids both N+1 loading and a Cartesian join between every annotation
and every library entry.

Concept references are reconstructed as `namespace@version_label:entry_key`. Point, time interval,
time-frequency box, and time-frequency polygon columns are mapped explicitly. Polygon vertices and
JSON attributes are preserved.

## Recording creation

`CreateRecordingRequest` validates the complete initial state and owns a stable
`initial_revision_id`. One transaction registers or verifies the immutable audio asset, inserts the
recording, resolves all pinned versions and concepts in bulk, inserts revision one with that ID,
inserts ordered pins and annotations, and conditionally sets the recording head. A retry whose
revision already exists for the same recording with a null parent returns that persisted snapshot.
A failure at any point rolls back the audio registration and the entire recording graph.

## Immutable revision saves

`SaveRecordingSnapshotRequest` carries complete editable state, a stable `new_revision_id`, and an
`expected_parent_revision_id`. Saving:

1. Checks whether `new_revision_id` is already persisted.
2. Returns that historical snapshot when it belongs to the same recording and expected parent, or
   raises `PersistenceIntegrityError` when the ID is bound to incompatible history.
3. Reads the recording, immutable audio metadata, current head, and parent revision number.
4. Rejects an already-stale expected parent.
5. Resolves all pinned versions and referenced entries in bounded bulk queries.
6. Constructs and validates the complete proposed `AnnotatedRecordingSnapshot`.
7. Inserts the supplied revision ID, ordered pins, and ordered annotations without changing old
   rows.
8. Advances `recordings.head_revision_id` with `IS NOT DISTINCT FROM` compare-and-swap semantics.
9. Commits the complete transaction.

A failed compare-and-swap raises `ConcurrentRevisionError`. Parent or revision-number uniqueness
races are translated to the same error. The runtime performs no update or delete against immutable
audio, library, revision, pin, or annotation rows.

## Validation boundaries

- External imports receive full model validation.
- GUI or application commands validate when their request models are constructed.
- A save constructs the complete proposed snapshot once before insertion, adding audio-bound
  validation to request-local checks.
- PostgreSQL remains the final structural and referential authority.
- Database reads use one centralized, tested hydration implementation.

The persistence layer verifies pinned content hashes, exact concept resolution, and permitted
geometry types. Library-specific JSON Schema validation belongs to the future interaction engine,
which can apply policy before calling the persistence boundary.

## Error translation

Application code receives `RecordingNotFoundError`, `RevisionNotFoundError`,
`LibraryVersionNotFoundError`, `ConcurrentRevisionError`, `DatabaseUnavailableError`, or another
`PersistenceError` subtype. Raw SQLAlchemy and driver exceptions are chained as causes for
debugging but never cross the public boundary directly.

The later local recovery queue will catch `DatabaseUnavailableError` and persist the deterministic
request, including its stable revision ID. Retrying the same request distinguishes an already
committed operation from a different writer: the former returns the matching persisted revision,
while a reused ID with another recording or parent raises `PersistenceIntegrityError`. No recovery
queue is implemented here.

## Audio URI handling

`AudioAsset.storage_uri` is the authoritative logical locator, normally
`registry-audio://assets/<uuid>`. `logical_path` is optional provenance or display data. PostgreSQL
stores the URI and audio metadata only. This layer returns the URI with every recording snapshot
but never opens, copies, decodes, streams, or stores audio bytes.

`AudioResolver` is only the separate `resolve(storage_uri: str) -> Path` protocol needed by a
future audio service.

## Semantic scope

The model contains paintable signal annotations and structural foreign keys only. It has no
semantic relation model, relation table, parent-child annotation edge, containment graph, or
generic graph behavior.

## Usage

Application code uses the boundary without importing SQLAlchemy:

```python
import os

from models import SaveRecordingSnapshotRequest
from persistence import create_persistence

with create_persistence(os.environ["REGISTRY_ALIGN_DATABASE_URL"]) as store:
    workspace = store.load_workspace(recording_id)
    saved = store.save_snapshot(
        SaveRecordingSnapshotRequest(
            recording_id=workspace.snapshot.recording_id,
            expected_parent_revision_id=workspace.snapshot.revision.id,
            name=workspace.snapshot.name,
            default_speaker_ref=workspace.snapshot.default_speaker_ref,
            language=workspace.snapshot.language,
            author="editor",
            message="Save current state",
            libraries=workspace.snapshot.libraries,
            annotations=workspace.snapshot.annotations,
        )
    )
```
