# PostgreSQL Schema

**Status:** Implemented

**Scope:** The canonical `registry_align` schema installed by Alembic

## Authority

The [baseline migration](../../alembic/versions/20260830_0001_snapshot_foundation.py) , [storage URI migration](../../alembic/versions/20260830_0002_storage_uri_nonempty.py) and [annotation label migration](../../alembic/versions/20260906_0003_annotation_label.py) are the executable schema definition. The [SQLAlchemy rows](../../src/persistence/sqlalchemy/rows.py) mirror that schema. This document explains the shape; it is not an alternate DDL source.

## Tables

| Table | Responsibility | Mutation contract |
| --- | --- | --- |
| `audio_assets` | Immutable audio identity, logical URI, hash, native timebase, and source metadata | Insert and read |
| `speakers` | Reusable speaker identity and metadata | Insert and read |
| `annotation_libraries` | Stable library identity and unique namespace | Insert and read |
| `library_versions` | Immutable published version metadata and content hash | Insert and read |
| `library_entries` | Ordered concepts belonging to one immutable library version | Insert and read |
| `recordings` | Stable recording identity, one audio asset, and current head pointer | Insert, read, and update `head_revision_id` only |
| `recording_revisions` | Immutable revision metadata and recording-level fields | Insert and read |
| `recording_revision_libraries` | Ordered library-version manifest for one revision | Insert and read |
| `annotations` | Ordered complete annotation state for one revision | Insert and read |

There is no separate annotation-identity table. `annotations.annotation_id` is stable across snapshots but is unique only within a recording revision through the composite primary key `(recording_revision_id, annotation_id)`.

## Audio and speakers

`audio_assets` stores a unique nonempty `storage_uri`, a unique lowercase SHA-256, optional provenance fields, positive sample rate, frame count, and channel count, JSON-object source metadata, and a creation timestamp. The canonical URI contract is:

```text
registry-audio://assets/<audio-asset-uuid>
```

The database enforces nonemptiness and uniqueness. URI-scheme and UUID validation belongs to the audio service.

`speakers` stores a required nonempty display name, optional unique external key, JSON-object metadata, and a creation timestamp. Recording revisions reference speakers through nullable `default_speaker_ref`.

## Libraries

`annotation_libraries.namespace` is globally unique and follows the domain namespace grammar. `library_versions` is unique by both `(library_id, version_label)` and `(library_id, content_sha256)`.

`library_entries` preserves version order with a nonnegative `position` that is unique within the version. Entry keys are unique within the version. `allowed_geometry_types` is a nonempty `TEXT[]` containing only the four supported geometry discriminators. Attribute schemas, validation hints, display hints, and metadata are JSON objects.

## Recordings and revisions

Each `recordings` row permanently references one `audio_assets` row. `head_revision_id` is nullable only during initial creation and is constrained with the recording ID so a recording cannot point to another recording's revision.

`recording_revisions` stores the request-owned UUID, recording ID, positive integer revision number, parent, schema version `1.0`, name, default speaker, language, timestamp, author, and message. The schema enforces:

- one revision number per recording;
- a null parent only for revision one;
- parents from the same recording;
- a unique non-null parent so history cannot branch;
- a head revision from the same recording.

`recording_revision_libraries` pins complete versions in stable position order. Its primary key prevents a version from being pinned twice and its position constraint prevents ambiguous order.

## Annotations

Each `annotations` row stores one occurrence in one complete revision:

- stable `annotation_id` and ordered `position`;
- exact `library_version_id` and `library_entry_id`;
- geometry discriminator and scalar bounds;
- polygon vertices when the geometry is a polygon;
- JSON-object attributes;
- optional label (`TEXT NULL`), confidence, note, and provenance reference.

The label names this annotation occurrence. It is stored directly on each revision annotation
row, not in concept attributes. Existing rows retain a null label after upgrading.

Composite foreign keys require the library version to be pinned by the same recording revision and the entry to belong to that version. Geometry checks enforce the required/null column shape, time and frequency ordering, finite frequency bounds, polygon-array shape, and confidence from zero through one.

Audio-frame bounds, polygon vertex bounds, unique annotation IDs in a snapshot, and pinned namespace-version membership are enforced by the domain model. Exact content hashes, concept resolution, and permitted geometry are checked by persistence before insertion. JSON Schema policy and Nyquist limits belong to the proposed application layer.

## Indexes

In addition to primary-key and unique-constraint indexes, the installed schema defines:

- optional speaker external-key lookup;
- recording lookup by audio asset;
- library-version history by library and creation time;
- recording-revision history by recording and creation time;
- the unique non-null revision parent;
- reverse lookup from pinned library version;
- stable annotation position within a revision;
- annotation timeline lookup by revision and start sample;
- annotation lookup by library entry and revision.

No blanket JSONB GIN, full-text, geometry-type, or range index is installed.

## Runtime permissions

Runtime permissions make canonical history insert-only for the application role. The exact grants,
deployment command, and ownership rules are maintained in the
[database foundation](PostgreSQL_Database_Foundation.md#runtime-role) and
[configure_runtime_role.sql](../../sql/configure_runtime_role.sql), outside Alembic because roles
and grants are environment-specific.
