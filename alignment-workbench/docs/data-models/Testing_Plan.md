# Signal Annotation Data Model Testing Plan

Status: Proposed

Scope: Domain, persistence, service, producer, and application-interface validation

## Audio and recording tests

- Identical audio bytes deduplicate into one audio asset.
- One recording permanently references one audio asset.
- Attempting to replace a recording's audio fails.
- Sample coordinates use the asset's native timebase.
- Derived duration agrees with frame count and sample rate.

## Library tests

- Namespace and version uniqueness are enforced.
- Published versions and entries cannot be mutated.
- Old entry references resolve after newer versions are published.
- An annotation cannot reference an entry outside the pinned manifest.
- Attribute schema validation covers valid and invalid values.
- Relation entries cannot be used as annotation concepts and annotation entries cannot be used as relations.
- Reusing an entry key in a new version does not change old-version semantics.
- A referenced library version cannot be deleted.

## Annotation geometry tests

- Point and time-interval geometry validates.
- Time-frequency box and polygon geometry validates.
- Negative and out-of-bounds sample positions fail.
- Invalid interval ordering fails.
- Above-Nyquist acoustic frequency fails.
- Polygon vertices outside their declared bounds fail.
- Geometry prohibited by the selected entry fails.
- Arbitrary annotation overlap succeeds.
- Unannotated gaps succeed.
- Competing concepts over identical geometry succeed.
- F0 events persist as sparse intervals with summary attributes.

## Annotation state tests

- Adding an annotation allocates a new identity.
- Editing retains annotation identity in the next revision.
- Deleting omits state only from the new revision.
- Confidence accepts null and inclusive values from zero through one.
- Invalid confidence fails.
- Opaque provenance references round-trip unchanged.
- Notes and entry-defined attributes round-trip unchanged.

## Recording revision tests

- First save creates revision 1 and sets the recording head.
- Each later save creates one complete immutable snapshot.
- Every explicit Save creates a revision, including an unchanged save.
- Revision numbers increase monotonically within a recording.
- Parent revisions belong to the same recording.
- Old revisions remain readable and unchanged.
- Restore creates a new head revision instead of deleting or rewinding later history.
- Optional author and message are retained.
- A failed save leaves the previous head unchanged.
- Concurrent save attempts allocate distinct revision numbers or fail cleanly without partial state.

## Relation tests

- Both endpoints must exist in the same submitted snapshot.
- Deleted endpoints cannot leave relations in the next snapshot.
- Relation identity belongs to the recording.
- Relation entries have the correct entry kind.
- Relation attribute schemas validate.
- Derived geometric relationships do not need stored relations.

## Repository and service tests

- Current snapshot reads resolve the head revision.
- Historical reads use the requested pinned library versions.
- History listings return compact metadata and correct annotation and relation counts.
- Save inserts the revision, manifest, annotation states, relation states, and head update atomically.
- Any validation or insert failure rolls back all new rows.
- Producers and GUI-facing services work through domain interfaces without importing table mappings.

## Producer integration tests

- MFA output imports as annotations without creating a required hierarchy.
- Pitch analysis imports only sparse pitch events, not the dense working track.
- Breath and silence remain distinct concepts.
- Unknown producer observations can use core unclassified entries.
- Human edits and analysis annotations coexist and overlap.
- Producer-specific attributes validate against the pinned producer library.

## Workbench and interchange tests

- Workbench save and load round-trip a snapshot without semantic loss.
- JSON export and import preserve recording, revision, audio, library, annotation, and relation identities.
- The complete example snapshot validates against the application DTO or schema when one exists.
- Overlapping annotations render without being rejected as tier collisions.
- Visibility filters do not mutate canonical state.
- Restoring a historical revision produces a new current revision in the UI.

## PostgreSQL integration tests

Run persistence tests against a disposable PostgreSQL database with the annotation-library schema applied before the recording schema. Verify foreign keys, checks, unique constraints, transaction rollback, and any immutability triggers introduced by the Alembic migrations.

Never point destructive persistence fixtures at a development or shared database.
