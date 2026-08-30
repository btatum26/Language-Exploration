# Recording PostgreSQL Schema

Status: Proposed

Scope: Audio assets, stable recordings, full revision snapshots, and annotations

## Requirements and dependencies

- PostgreSQL 16 or later
- `pgcrypto` for `gen_random_uuid()`
- JSONB
- Standard foreign keys, checks, and indexes
- Alembic migrations through `registry-aligner`

The annotation-library tables must exist before recording and annotation tables are created. The complete representative DDL is in [recording_schema.sql](sql/recording_schema.sql). It fixes the intended column shapes and constraints but is not a substitute for an Alembic migration.

## Audio assets table

`audio_assets` stores immutable signal identity and timebase metadata.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `sha256` | VARCHAR(64) | Unique, required |
| `storage_key` | TEXT | Unique, required |
| `logical_path` | TEXT | Optional display or import path |
| `media_type` | TEXT | Optional |
| `original_extension` | TEXT | Optional |
| `codec` | TEXT | Optional |
| `sample_rate_hz` | INTEGER | Positive, required |
| `channels` | INTEGER | Positive, required |
| `frame_count` | BIGINT | Positive, required |
| `source_metadata` | JSONB | Required, default `{}` |
| `created_at` | TIMESTAMPTZ | Required, default now |

Duration is derived as `frame_count / sample_rate_hz` and is not duplicated as authoritative state.

## Speakers table

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `external_key` | TEXT | Unique optional stable key |
| `display_name` | TEXT | Required |
| `metadata` | JSONB | Required, default `{}` |
| `created_at` | TIMESTAMPTZ | Required, default now |

## Recordings table

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `audio_asset_id` | UUID | Required FK to `audio_assets` |
| `head_revision_id` | UUID | Nullable initially; FK added after the revision table |
| `created_at` | TIMESTAMPTZ | Required, default now |

Changing `audio_asset_id` after creation is prohibited by the service and should also be prevented by a database trigger or repository rule.

## Recording revisions table

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `recording_id` | UUID | Required FK to `recordings` |
| `revision_number` | BIGINT | Positive and unique per recording |
| `parent_revision_id` | UUID | Nullable FK to `recording_revisions` |
| `name` | TEXT | Required |
| `default_speaker_id` | UUID | Nullable FK to `speakers` |
| `language_tag` | TEXT | Required BCP 47 string |
| `author` | TEXT | Optional |
| `message` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Required, default now |

Constraints and service rules:

- Unique `(recording_id, revision_number)`
- The first revision has no parent.
- Later revisions point to a revision of the same recording.
- Revision rows are immutable after insertion.

## Recording revision libraries table

`recording_revision_libraries` is the pinned dependency manifest.

| Column | Type | Rules |
| --- | --- | --- |
| `recording_revision_id` | UUID | FK, part of primary key |
| `library_version_id` | UUID | FK, part of primary key |

An annotation in a revision may reference only an entry belonging to one of these pinned versions.

## Annotation identities table

`annotation_identities` establishes stable logical identity independently of per-revision state.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `recording_id` | UUID | Required FK to `recordings` |
| `created_at` | TIMESTAMPTZ | Required, default now |

An annotation identity can never move to another recording.

## Signal annotation states table

Each `signal_annotation_states` row is the state of one annotation in one full recording revision.

| Column | Type | Rules |
| --- | --- | --- |
| `recording_revision_id` | UUID | FK, part of primary key |
| `annotation_id` | UUID | FK, part of primary key |
| `library_entry_id` | UUID | Required FK to exact immutable entry |
| `geometry_type` | TEXT | Checked geometry type |
| `start_sample` | BIGINT | Required, non-negative |
| `end_sample` | BIGINT | Nullable depending on geometry |
| `min_frequency_hz` | DOUBLE PRECISION | Nullable depending on geometry |
| `max_frequency_hz` | DOUBLE PRECISION | Nullable depending on geometry |
| `geometry_data` | JSONB | Required, default `{}` |
| `attributes` | JSONB | Required, default `{}` |
| `confidence` | DOUBLE PRECISION | Nullable, `[0,1]` |
| `provenance_ref` | TEXT | Optional opaque value |
| `note` | TEXT | Optional |

The primary key is `(recording_revision_id, annotation_id)`.

Database checks cover geometry discriminators, non-negative starts, end ordering, frequency-column shape, frequency ordering, and confidence range. The application service enforces audio duration, Nyquist bounds, polygon validity, allowed geometry, manifest membership, and JSON Schema validation.

## Recommended indexes

At minimum:

```text
audio_assets(sha256) UNIQUE
recording_revisions(recording_id, revision_number DESC) UNIQUE
signal_annotation_states(recording_revision_id, start_sample, end_sample)
signal_annotation_states(library_entry_id)
signal_annotation_states(annotation_id, recording_revision_id)
```

Add a GIN index on `attributes` only after query patterns justify it. A GiST range index may later accelerate interval-overlap queries but is not required initially.

## Immutability

Database triggers or repository rules should reject updates and deletes to recording revisions and saved annotation states. Ordinary application behavior inserts a complete new revision and then atomically advances `recordings.head_revision_id`.
