# Annotation Library Data Model

**Status:** Implemented model and persistence shape; concrete library contents remain proposed

**Scope:** Reusable annotation meanings, immutable versions, and concept references

## Purpose

Geometry says where an annotation is. An annotation-library entry says what it means.

Labels such as `a`, `tap`, or `pitch-fall` are ambiguous without a pinned library reference. Different producers may use the same label with different definitions, so a recording annotation points to one exact immutable entry instead of copying a definition or relying on a bare label.

## Library identity

Each library has:

- Stable UUID
- Unique namespace
- Human-readable name
- Optional description
- Optional owner or producer label

Namespaces use the same grammar as the namespace component of `ConceptRef`: they begin with an
ASCII letter or digit and then contain only ASCII letters, digits, `.`, `_`, or `-`.

Example namespaces include:

```text
le.core
le.prosody
le.spectral-events
producer.mfa.italian
standard.ipa
project.italian-exploration
```

## Library version

Every published `LibraryVersion` is immutable. A version contains:

- Library-version UUID
- Parent library UUID
- Version label such as `1.0.0`
- Lowercase hexadecimal content SHA-256
- Creation timestamp
- Optional author and description
- Complete entry set

Semantic versioning is useful but not required. A library may use semantic versions, producer
model versions, dates, or opaque version labels. A version label cannot contain whitespace or
`:` so it is always valid inside a `ConceptRef`.

## Library entry

Each immutable entry contains:

- Entry UUID
- Library-version UUID
- Stable entry key within that version
- Display name
- Description
- One or more allowed geometry types
- JSON Schema for occurrence attributes
- Optional validation hints
- Optional display hints
- Arbitrary metadata

Entry keys use the same grammar as namespaces so they are always valid as the final component of
a `ConceptRef`.

An annotation entry may look like:

```json
{
  "entry_key": "pitch-fall",
  "display_name": "Pitch lowering",
  "description": "A sustained decline in estimated fundamental frequency.",
  "allowed_geometry_types": ["time_interval"],
  "attribute_schema": {
    "type": "object",
    "properties": {
      "start_hz": {"type": "number", "exclusiveMinimum": 0},
      "end_hz": {"type": "number", "exclusiveMinimum": 0},
      "delta_semitones": {"type": "number"},
      "shape": {"type": "string"}
    },
    "additionalProperties": true
  },
  "display_hints": {
    "color": "#5C6BC0",
    "preferred_surface": "waveform"
  }
}
```

Display hints are defaults, not semantic truth. An application may ignore them.

## Paintable concepts

Every library entry defines a concept that can be painted over audio as a `SignalAnnotation`.
Examples include:

- `core:silence`
- `core:breath`
- `prosody:pitch-fall`
- `mfa-italian:word`
- `ipa:open-front-unrounded-vowel`
- `spectral:broadband-transient`

## Concept pointers

PostgreSQL annotations point directly to an immutable entry UUID. JSON interchange uses a human-readable reference:

```text
namespace@version:entry-key
```

For example:

```text
le.prosody@1.0.0:pitch-fall
```

Each recording revision also pins a manifest of every library version used in its snapshot. This makes dependencies inspectable and keeps exported snapshots portable.

The snapshot rejects duplicate pinned `namespace@version` pairs and annotations whose concept
namespace and version are absent from that manifest.

## Unclassified entries

A future core library should include at least:

```text
unclassified-point-event
unclassified-interval-event
unclassified-time-frequency-event
```

These entries preserve unusual evidence without prematurely forcing it into IPA or another established category.

## Attribute validation

An entry's `attribute_schema` defines valid occurrence-specific attributes. The database stores attributes as JSONB. JSON Schema evaluation against the exact pinned entry belongs to the proposed application layer.

Optional validation hints may describe minimum duration, ranges, producer conventions, or visual grouping. Library-specific rules must not silently create a universal linguistic hierarchy.

## Library version history

Library history is an append-only sequence of immutable published versions.

- Adding, removing, or redefining an entry publishes a new version.
- Published library versions and entries are never updated in place.
- An entry key may reappear in a new version, but its meaning is fixed by the pinned version.
- A changed definition creates a new entry row in the new version.
- Old versions remain available while any recording revision references them.
- A recording revision may reference multiple library versions at once.
- Libraries define reusable meanings; annotations define occurrences.

Old recording revisions always resolve their entries using the versions they originally pinned. Publishing a new version never reinterprets saved data.

## Deferred decisions

The foundation does not decide:

- Exact contents of IPA, breath, spectral-event, or prosody libraries
- Automatic equivalence among entries from different producers
- Reusable word lexicons
- Ontology inference
- A library marketplace or distribution protocol
