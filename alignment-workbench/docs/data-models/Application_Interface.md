# Application Interface

Status: Proposed

Scope: Snapshot interchange, save and read workflows, service boundaries, and editor behavior

## Canonical snapshot

The application-facing `AnnotatedRecordingSnapshot` is portable and independent of SQLAlchemy. It contains:

- Schema version and recording ID
- Revision metadata
- Recording name, default speaker, and language
- Immutable audio-asset identity and timebase
- Pinned library manifest
- Complete annotation state
- Complete relation state

A short shape example is:

```json
{
  "schema_version": "1.0",
  "recording_id": "4de8f232-c443-49df-b411-143add24952f",
  "revision": {"number": 4, "parent_id": "03157330-bb8d-4bc7-9a96-6cbba8230f6c"},
  "audio_asset": {"sample_rate_hz": 48000, "frame_count": 192000},
  "libraries": [{"namespace": "le.prosody", "version": "1.0.0"}],
  "annotations": [],
  "relations": []
}
```

The complete payload example is in [annotated_recording_snapshot.json](examples/annotated_recording_snapshot.json).

## Save request

The client sends complete current snapshot state:

- Recording ID
- Name
- Default speaker
- Language
- Optional author and message
- Complete library manifest
- Complete annotations
- Complete relations

The client does not send diffs or editing commands.

## Save validation

Before inserting data, the service validates:

- The recording exists and its audio asset is unchanged.
- Existing annotation and relation identities belong to this recording; new ones are allocated explicitly.
- Geometry lies within the audio frame count and frequency limits.
- Polygon geometry is valid.
- Every concept reference resolves to an immutable library entry.
- Every referenced library version is in the submitted manifest.
- Entry kind and geometry type are correct.
- Attributes validate against the exact entry's JSON Schema.
- Confidence values are valid.
- Relation endpoints exist in the submitted snapshot.

## Save transaction

Saving one revision is one PostgreSQL transaction:

1. Lock the `recordings` row briefly with `SELECT ... FOR UPDATE`.
2. Read the current head and allocate `revision_number = previous + 1`.
3. Insert new annotation identity rows.
4. Insert new relation identity rows.
5. Insert the immutable `recording_revisions` row.
6. Insert the complete pinned library manifest.
7. Bulk insert the complete annotation-state snapshot.
8. Bulk insert the complete relation-state snapshot.
9. Update `recordings.head_revision_id`.
10. Commit.

Any failure rolls back the entire save and leaves the previous head unchanged.

Version one creates a revision on every explicit Save, including an unchanged save. This preserves user intent and avoids hidden snapshot-hash behavior.

## Read current snapshot

The service:

1. Resolves `recordings.head_revision_id`.
2. Loads stable recording and audio-asset metadata.
3. Loads revision metadata.
4. Loads pinned library versions.
5. Loads all annotation states for the revision.
6. Loads all relation states for the revision.
7. Optionally resolves entry definitions for display.
8. Returns one `AnnotatedRecordingSnapshot`.

Use several bounded queries rather than one large Cartesian join.

## Read history

A historical read uses a specific revision ID or revision number instead of the head. It never reinterprets concept pointers through newer library versions.

History listing returns compact rows containing revision ID and number, timestamp, author, message, annotation count, and relation count. Detailed diffs are calculated only when explicitly requested.

Restoring an old revision loads its complete snapshot and saves it as a new head revision. It never deletes or rewinds later history.

## Repository boundaries

The GUI, CLI, and analysis producers do not use SQLAlchemy tables directly. The intended interfaces are:

```text
RecordingRepository
    create_recording(...)
    get_head_snapshot(recording_id)
    get_revision_snapshot(recording_id, revision_number)
    list_revisions(recording_id)
    save_snapshot(snapshot_request)
    restore_revision(recording_id, revision_number, author, message)

LibraryRepository
    create_library(...)
    publish_library_version(...)
    resolve_entry(namespace, version, entry_key)
    get_library_version(...)
    list_library_versions(...)
    find_compatible_entries(...)
```

These names describe the application contract rather than a complete source file. The save service owns validation and transaction orchestration.

## Workbench behavior

The editor displays annotations as overlay paints rather than a forced word-and-phone tree. Minimum behavior is:

- Load one snapshot revision.
- Resolve concept names and default display hints.
- Show overlapping annotations without collision errors.
- Filter by library, concept, producer namespace, or geometry type.
- Paint point, time interval, time-frequency box, and polygon annotations.
- Edit geometry, concept pointer, attributes, confidence, provenance reference, and note.
- Add or remove explicit relations.
- Save complete current state as the next revision.
- Browse and restore historical revisions.

Optional lanes may improve readability, but lanes are a view concern and are not canonical containment tiers.

## Validation ownership

The database enforces:

- Referential integrity and UUID identities
- Unique namespaces, versions, entry keys, and recording revision numbers
- Basic geometry-column shape and bounds ordering
- Positive frame and sample-rate values
- Confidence range
- Valid discriminator strings
- Relation endpoints in the same snapshot

The application service enforces:

- Audio immutability
- Identity ownership by recording
- Parent revision ownership
- Sample bounds and Nyquist limits
- Polygon validity
- Library manifest membership
- Entry kind and permitted geometry
- Attribute JSON Schema validation
- Immutable-row behavior
- Full snapshot completeness

Libraries may additionally define required attributes, ranges, recommended minimum duration, producer label conventions, and suggested visual grouping. These rules remain library-specific.
