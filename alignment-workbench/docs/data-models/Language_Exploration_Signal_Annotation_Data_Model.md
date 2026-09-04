# Language Exploration Signal Annotation Data Model

**Status:** Implemented canonical domain model

**Scope:** Source-of-truth representation independent of persistence infrastructure

**Primary use:** A fully painted, signal-first representation of an immutable audio recording

## Overview

Language Exploration uses a neutral source model into which independent producers can place observations about recorded sound. The audio signal is primary; every other item is a painted annotation over that signal.

The canonical saved object is an **Annotated Recording Snapshot**:

> One immutable audio asset, recording metadata, a pinned set of annotation-library definitions, and the complete annotations that constitute one saved revision.

The model records the current painted state. It does not execute analysis, retain analysis jobs, or store dense frame-by-frame feature tracks.

## Documents

- [Recording data model](Recording_Data_Model.md) describes recordings, annotations, geometry, and recording version history.
- [Annotation library data model](Annotation_Library_Data_Model.md) describes reusable annotation meanings and immutable library version history.
- [Analysis notes](Analysis_Notes.md) defines the analysis boundary, sparse event representation, producer integration, and current-data migration.

## Supporting files

- [Annotated recording snapshot example](examples/annotated_recording_snapshot.json) contains the complete JSON example.

## Related documentation

- [Application API](../application-api/Alignment_Workbench_Unified_Application_API.md) defines the implemented handlers and editing session.
- [Persistence architecture](../persistence/Persistence_Architecture.md) defines implemented storage ownership.
- [PostgreSQL schema](../persistence/PostgreSQL_Schema.md) documents the installed Alembic schema.
- [Data model and persistence testing](../testing/Data_Model_and_Persistence_Testing.md) describes current automated coverage.

## Foundational invariants

- The audio signal is primary and immutable.
- A recording is neutral and is not inherently an utterance, sentence, phrase, or word.
- A saved revision is a complete painted snapshot.
- Annotations are sparse and may overlap freely.
- Every annotation points to one exact immutable library entry.
- Libraries define reusable meaning; annotations describe occurrences.
- Time is stored in integer audio samples.
- F0 is represented as sparse painted events, never as saved frame-by-frame data.
- Analysis execution remains outside the source model.
- History is linear and snapshot-based rather than diff-based; stale concurrent saves are rejected.
- Deleting an annotation never destroys an older revision.
- Linguistic hierarchies are optional interpretations, not the foundation of the data.
