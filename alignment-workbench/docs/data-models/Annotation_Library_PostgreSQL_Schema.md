# Annotation Library PostgreSQL Schema

Status: Proposed

Scope: Persistent library identity, immutable versions, and immutable entries

## Requirements

- PostgreSQL 16 or later
- `pgcrypto` for `gen_random_uuid()`
- JSONB
- Standard foreign keys, checks, and unique constraints
- Alembic migrations through `registry-aligner`

PostgreSQL enum types are not required. Checked text columns are easier to evolve through migrations.

The complete representative DDL is in [annotation_library_schema.sql](sql/annotation_library_schema.sql). The SQL fixes the intended column shapes and constraints but is not a substitute for an Alembic migration.

## Annotation libraries table

`annotation_libraries` stores stable identity and namespace information.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `namespace` | TEXT | Unique, required |
| `name` | TEXT | Required |
| `description` | TEXT | Optional |
| `owner_label` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Required, default now |

## Annotation library versions table

`annotation_library_versions` stores immutable publications under a stable library.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `library_id` | UUID | Required FK to `annotation_libraries` |
| `version_label` | TEXT | Required |
| `content_sha256` | VARCHAR(64) | Required |
| `author` | TEXT | Optional |
| `description` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Required, default now |

Constraints:

- Unique `(library_id, version_label)`
- Unique `(library_id, content_sha256)`
- A 64-character content hash
- Immutable rows after publication

## Annotation library entries table

`annotation_library_entries` stores the exact targets referenced by annotations and relations.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key and exact concept pointer target |
| `library_version_id` | UUID | Required FK to `annotation_library_versions` |
| `entry_key` | TEXT | Required |
| `entry_kind` | TEXT | `annotation` or `relation` |
| `display_name` | TEXT | Required |
| `description` | TEXT | Required |
| `allowed_geometry_types` | JSONB | Required array for annotation entries |
| `attribute_schema` | JSONB | Required JSON Schema, default object schema |
| `validation_hints` | JSONB | Required, default `{}` |
| `display_hints` | JSONB | Required, default `{}` |
| `metadata` | JSONB | Required, default `{}` |

Constraints:

- Unique `(library_version_id, entry_key)`
- `entry_kind IN ('annotation', 'relation')`
- Relation entries have an empty allowed-geometry array.
- Rows are immutable after publication.

## Recording integration

Recording revisions pin library versions through `recording_revision_libraries`, described in [Recording PostgreSQL Schema](Recording_PostgreSQL_Schema.md). Annotation and relation state rows reference `annotation_library_entries.id` directly.

The application service enforces that:

- Every referenced entry belongs to a library version pinned by the recording revision.
- Annotation state references an annotation entry.
- Relation state references a relation entry.
- Geometry type is permitted by the entry.
- Occurrence attributes validate against the entry's JSON Schema.

## Immutability

Ordinary application behavior never updates or deletes published library versions or entries. Database triggers or repository rules should reject those operations. Old versions cannot be deleted while a recording revision references them.
