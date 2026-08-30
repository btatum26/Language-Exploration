# Language Exploration Signal Annotation Data Model

Status: Proposed canonical data model

Scope: Source-of-truth representation and PostgreSQL persistence

Primary use: A fully painted, signal-first representation of an immutable audio recording

## Overview

Language Exploration uses a neutral source model into which independent producers can place observations about recorded sound. The audio signal is primary; every other item is a painted annotation over that signal.

The canonical saved object is an **Annotated Recording Snapshot**:

> One immutable audio asset, recording metadata, a pinned set of annotation-library definitions, and the complete annotations that constitute one saved revision.

The model records the current painted state. It does not execute analysis, retain analysis jobs, or store dense frame-by-frame feature tracks.

## Documents

- [Recording data model](Recording_Data_Model.md) describes recordings, annotations, geometry, and recording version history.
- [Recording PostgreSQL schema](Recording_PostgreSQL_Schema.md) describes persistence for audio assets, recordings, revision snapshots, and annotations.
- [Annotation library data model](Annotation_Library_Data_Model.md) describes reusable annotation meanings and immutable library version history.
- [Annotation library PostgreSQL schema](Annotation_Library_PostgreSQL_Schema.md) describes persistence for libraries, versions, and entries.
- [Analysis notes](Analysis_Notes.md) defines the analysis boundary, sparse event representation, producer integration, and current-data migration.
- [Application interface](Application_Interface.md) defines snapshot interchange, save/read workflows, service boundaries, validation, and workbench behavior.
- [Testing plan](Testing_Plan.md) defines unit, persistence, integration, and round-trip coverage.

## Supporting files

- [Annotation library schema SQL](sql/annotation_library_schema.sql) contains the representative library DDL.
- [Recording schema SQL](sql/recording_schema.sql) contains the representative recording and annotation DDL.
- [Annotated recording snapshot example](examples/annotated_recording_snapshot.json) contains the complete JSON example.

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
- History is linear, single-user, and snapshot-based rather than diff-based.
- Deleting an annotation never destroys an older revision.
- Linguistic hierarchies are optional interpretations, not the foundation of the data. 
