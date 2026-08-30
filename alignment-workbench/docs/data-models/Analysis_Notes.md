# Analysis Notes

Status: Proposed

Scope: Analysis boundaries, sparse event output, producer adapters, and current-data migration

## Analysis boundary

The signal annotation model stores durable observations, not the process that produced them. Analysis runs, logs, temporary artifacts, model leases, caches, queues, and dense working arrays remain in external analysis systems.

An annotation may retain an opaque `provenance_ref` so another subsystem can trace it. The recording model does not resolve or interpret that reference.

## Producer responsibility

An analysis producer translates external output into annotation-library entries and `SignalAnnotation` objects. Before publishing an annotation, it must resolve the intended semantic entry.

A producer may:

- Reuse an existing compatible entry.
- Add an entry by publishing a new project-library version.
- Publish a producer-specific library version.
- Use a core unclassified entry when an event is not yet understood.

The recording service validates concept pointers but does not decide which concept a producer should choose. Producers construct domain objects and call the application service; they do not write SQL directly.

## Sparse F0 events

No frame-by-frame pitch track is stored in a recording snapshot. A detector performs dense pitch analysis externally, discards or separately manages the working series, and publishes sparse painted intervals such as:

- `pitch-fall`
- `pitch-rise`
- `steady-pitch`
- Future `pitch-reset`, `pitch-peak`, or `pitch-valley` entries

A saved event may carry useful summary measurements:

```json
{
  "concept_ref": "le.prosody@1.0.0:pitch-fall",
  "geometry": {
    "type": "time_interval",
    "start_sample": 42100,
    "end_sample": 49800
  },
  "attributes": {
    "start_hz": 171.2,
    "end_hz": 124.7,
    "delta_hz": -46.5,
    "delta_semitones": -5.48,
    "shape": "approximately-linear"
  }
}
```

Only the painted event and useful summaries are part of the canonical recording state.

## Montreal Forced Aligner

MFA may publish word, phone, and silence hypothesis intervals. Its output is one producer's painted interpretation, not a canonical hierarchy.

A producer library might expose:

```text
producer.mfa.italian@3.3.7:word
producer.mfa.italian@3.3.7:phone-a
producer.mfa.italian@3.3.7:phone-v
producer.mfa.italian@3.3.7:silence
```

Word text and backend-specific values belong in occurrence attributes.

## Breath and silence detectors

Breath and silence detectors publish interval annotations. They do not fill every gap unless their own semantics require that behavior. Breath and silence are distinct concepts; a quiet interval is not automatically a breath.

## Human painting

A user selects a library entry, paints compatible geometry, adjusts attributes, and saves a new full recording revision. Unknown observations use core unclassified entries rather than forcing a premature classification.

## Current-model migration notes

The current recording and audio infrastructure can be partially reused, but the existing `segments` table does not remain the canonical annotation model.

Reusable concepts include:

- Content-addressed source audio
- SHA-256 verification
- PostgreSQL unit-of-work boundaries
- Sample-based interval timing
- Speaker records
- Remote audio resolution
- GUI and backend separation
- Atomic transactions

Concepts to replace or demote include:

- `AlignmentSegment` becomes a producer-output DTO rather than the canonical entity.
- A fixed `segments.kind` set becomes a library-entry reference.
- `parent_segment_id` becomes an optional typed relation where semantic information requires it.
- Same-tier overlap QC leaves core validation.
- `segment_revisions` become full recording-revision snapshots.
- `alignment_results` may remain in an external analysis subsystem but is not required by the source model.

For each current recording, migration should:

1. Reuse or migrate its audio asset.
2. Create one stable new `Recording`.
3. Publish or import an MFA library version describing its current labels.
4. Resolve the current effective model-plus-revision topology.
5. Convert each effective word, phone, silence, and utterance interval into a `SignalAnnotation`.
6. Save the result as recording revision 1.

The existing detailed revision chain may be archived separately. It does not need to be reconstructed before the current effective state can be imported.

## Deferred analysis questions

- Dense measurement storage outside recording snapshots
- Automatic equivalence between producer-specific entries
- Provenance subsystem identifiers and retention policy
- Additional producer adapters and their library ownership
