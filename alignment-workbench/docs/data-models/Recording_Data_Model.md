# Recording Data Model

**Status:** Implemented in `src/models.py` and the PostgreSQL persistence layer

**Scope:** Stable recording identity, immutable saved revisions, and signal annotations

## Purpose

A recording is a neutral annotated document over one exact audio asset. It is deliberately not called an utterance, phrase, sentence, or statement because those terms impose interpretations that belong in annotations.

Annotations may describe word or phone hypotheses, pitch events, breaths, pauses, spectral events, unclassified noises, or future categories. They may overlap, disagree, or cover only part of a recording. The core model does not require a linguistic containment hierarchy.

## Version-one assumptions

- Concurrent editors may load the same revision. Stale saves are rejected; merging, permissions, and collaborative locking are out of scope.
- Revision history is linear. Every Save creates the next immutable recording revision.
- Each revision is a full snapshot, not a list of diffs or commands.
- Audio is immutable. Edited or replaced audio creates a new recording.
- All authoritative time coordinates use integer sample positions.
- Annotations are sparse and may overlap freely.
- There is no required hierarchy among phrase, word, phone, silence, pitch, or other annotations.
- Analysis execution and dense feature tracks remain outside this model.
- An optional human-readable author does not require a user-account system.
- One default speaker and one default language are sufficient for version one.

## Non-goals

- A universal linguistic ontology
- A fixed utterance, phrase, word, syllable, and phone hierarchy
- Analysis pipeline or job orchestration
- Dense pitch, formant, energy, or spectrogram arrays
- Model-training data and checkpoints
- Temporary analysis files
- Branches, merges, rebases, or diff-based commits
- Per-field or per-annotation event sourcing
- Real-time collaborative editing
- A requirement that silence fill every unannotated gap

## Audio asset

An `AudioAsset` identifies the exact immutable audio bytes being annotated. It contains storage and timebase metadata but no transcript or annotations.

Required properties are:

- Stable ID
- Lowercase hexadecimal SHA-256 of the original bytes
- Nonempty logical `storage_uri`, normally `registry-audio://assets/<uuid>`
- Media type, container, codec, or original extension when available
- Sample rate
- Channel count
- Frame count
- Duration derived from frame count and sample rate

The audio hash is the ultimate identity of the signal. Machine-specific absolute paths are never authoritative.

## Speaker

A `Speaker` is a reusable identity referenced by recording revisions. Version one requires a stable speaker ID, a display name, and optional metadata. Speaker metadata provides descriptive context; it does not determine annotation semantics.

## Recording

A `Recording` is the stable identity of an annotated document over one audio asset. It contains:

- Recording ID
- Immutable `AudioAsset` reference
- Current or head revision reference
- Creation timestamp

Correctable values do not belong to the stable recording row. Display name, speaker, language, notes, library imports, and annotations belong to each revision snapshot.

## Recording revision

A `RecordingRevision` is one immutable saved snapshot of the fully painted recording. It contains:

- Revision ID
- Recording ID
- Monotonic revision number within the recording
- Parent revision ID, except for the first revision
- Display name
- Default speaker reference
- Default language tag
- Optional author and save message
- Timestamp
- Complete pinned library manifest
- Complete annotation state

The parent link expresses linear history. It is not a branch or merge mechanism.

## Signal annotation

A `SignalAnnotation` is one painted occurrence over the audio. It has:

- Stable annotation ID across recording revisions
- Exact annotation-library entry reference
- Geometry
- Instance attributes
- Optional confidence
- Optional occurrence label for display
- Optional note
- Optional opaque provenance reference

Annotations are independent painted occurrences. The core model does not store links between them.

## Coordinate model

All authoritative temporal locations are integer sample positions in the attached audio asset's native timebase. Intervals use half-open bounds:

```text
[start_sample, end_sample)
```

The start sample is included and the end sample is excluded. This makes adjacent annotations with a shared boundary unambiguous. Seconds are derived for display and interchange:

```text
seconds = sample_position / audio_asset.sample_rate_hz
```

## Geometry types

### Point

A point represents an instantaneous landmark or boundary such as burst onset, voicing onset, a phrase-boundary hypothesis, or a click location. `start_sample` is required and `end_sample` is null.

### Time interval

A time interval represents an event or state extending over time, such as a phone or word hypothesis, breath, silence, pitch movement, or voiced frication. `start_sample` and an `end_sample` greater than the start are required.

### Time-frequency box

A time-frequency box represents an approximately rectangular region such as a short broadband transient, concentrated band energy, or painted frication. It requires start and end samples plus minimum and maximum frequencies.

### Time-frequency polygon

A time-frequency polygon represents a non-rectangular region. It stores time and frequency bounds plus ordered vertices in physical coordinates rather than pixels.

```json
{
  "vertices": [
    {"sample": 42100, "frequency_hz": 200.0},
    {"sample": 42580, "frequency_hz": 4100.0},
    {"sample": 42420, "frequency_hz": 4800.0}
  ]
}
```

An optional rendering transform may be retained in annotation attributes, but it does not replace physical coordinates.

## Geometry invariants

Geometry models and `AnnotatedRecordingSnapshot` enforce:

- `start_sample >= 0`
- Point positions are less than the audio frame count.
- Interval ends are greater than starts and no greater than the frame count.
- Frequencies are non-negative and maximum frequency exceeds minimum frequency.
- Polygon vertices lie inside the stored bounding box.
- Annotation IDs are unique within the snapshot.
- Pinned namespace and version pairs are unique within the snapshot.
- Every annotation's concept namespace and version are pinned by the snapshot.

The implemented persistence layer resolves the exact library entry, verifies its pinned version
and content hash, and checks that the geometry type is allowed. Nyquist checks and entry-defined
JSON Schema policy belong to the proposed application layer.

The core model allows exact geometric duplicates, partial overlap, full containment, cross-category overlap, competing annotations, and unannotated gaps. Optional library rules may be stricter, but they do not become universal database constraints.

## Label, attributes, confidence, provenance, and notes

`SignalAnnotation.label` is optional free text naming this occurrence. It is independent of the
concept reference, library-defined attributes, and note. Labels belong to revision snapshots and
follow the normal edit/save history. Existing JSON without `label` loads with `label=None`; the
GUI displays **Unlabeled** when the label is absent or blank.

`attributes` is JSON containing occurrence-specific values such as word text, a producer phone label, pitch summaries, breath direction, detector measurements, or optional display parameters. It must validate against the referenced library entry's JSON Schema.

`confidence` is optional and constrained to `[0, 1]`. The library definition explains what confidence means for that concept or producer.

The optional `provenance_ref` is opaque to this model. Examples include:

```text
manual:ben
mfa-result:75c8...
pitch-detector-result:1ae2...
import:textgrid:sha256:...
```

A short free-text note may capture occurrence-specific observations. It must not substitute for a reusable library definition.

## Recording version history

Every explicit Save creates a complete immutable revision containing all current annotations. The system stores snapshots rather than operations.

```text
Revision 1: A, B, C
Revision 2: A, B-modified, C, D
Revision 3: A, B-modified, D
```

A history view may derive that D was added and C was deleted. Those differences are not the persistence model.

### Stable identities

- Adding an annotation allocates a new annotation UUID.
- Modifying an annotation retains its UUID and saves new state in the next snapshot.
- Deleting an annotation omits it from the next snapshot.
- Restoring an annotation saves prior state into a new revision.

Old revisions remain immutable, so deletion never destroys history.

### Revision metadata

Each revision stores its number, parent revision, timestamp, optional author, and optional save message. `recordings.head_revision_id` identifies the current revision. There are no branches or merges.

### Restoring an old revision

Restore does not move the head backward or delete later history. To restore revision 3 while revision 7 is current, the service loads revision 3 and saves that state as revision 8, with a message such as `Restore revision 3`.

### Concurrent save behavior

Every save request carries a stable `new_revision_id` and the `expected_parent_revision_id` loaded
by the editor. If the new ID already belongs to the same recording and parent, retry returns that
committed revision. Incompatible ID reuse is an integrity error. Otherwise the save compares the
current head with the expected parent; a mismatch rejects it as stale without creating a revision.
A match allocates the next revision number and advances the head with compare-and-swap semantics.
The initial create request likewise owns a stable `initial_revision_id` and uses a null parent.

## Deferred decisions

The foundation does not decide:

- Multi-speaker annotations
- Per-annotation language overrides
- Dense measurement storage
- Branching or collaborative revision control
- Advanced geometric masks
