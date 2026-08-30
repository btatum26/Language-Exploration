# Annotation Library PostgreSQL Schema

Status: Proposed

Scope: Persistent library identity, immutable versions, and immutable entries

## Requirements

- PostgreSQL 16 or later
- `pgcrypto` for `gen_random_uuid()`
- JSONB
- Standard foreign keys, checks, and unique constraints
- Alembic migrations owned by `alignment-workbench`

PostgreSQL enum types are not required. Checked text columns are easier to evolve through migrations.

The complete representative DDL is in [annotation_library_schema.sql](sql/annotation_library_schema.sql). The SQL fixes the intended column shapes and constraints but is not a substitute for an Alembic migration.

## Annotation libraries table

`annotation_libraries` stores stable identity and namespace information.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `namespace` | TEXT | Unique, required, concept-compatible identifier |
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
| `version_label` | TEXT | Required, no whitespace or `:` |
| `content_sha256` | VARCHAR(64) | Required lowercase hexadecimal SHA-256 |
| `author` | TEXT | Optional |
| `description` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | Required, default now |

Constraints:

- Unique `(library_id, version_label)`
- Unique `(library_id, content_sha256)`
- A 64-character lowercase hexadecimal content hash
- Immutable rows after publication

## Annotation library entries table

`annotation_library_entries` stores the exact concepts referenced by annotations.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | UUID | Primary key and exact concept pointer target |
| `library_version_id` | UUID | Required FK to `annotation_library_versions` |
| `entry_key` | TEXT | Required, concept-compatible identifier |
| `display_name` | TEXT | Required |
| `description` | TEXT | Required |
| `allowed_geometry_types` | JSONB | Required non-empty array of geometry discriminators |
| `attribute_schema` | JSONB | Required JSON Schema, default object schema |
| `validation_hints` | JSONB | Required, default `{}` |
| `display_hints` | JSONB | Required, default `{}` |
| `metadata` | JSONB | Required, default `{}` |

Constraints:

- Unique `(library_version_id, entry_key)`
- Namespace and entry key begin with an ASCII alphanumeric and otherwise contain only ASCII alphanumerics, `.`, `_`, or `-`.
- Version labels contain neither whitespace nor `:`.
- Allowed geometry is a non-empty array containing only supported geometry discriminators.
- Rows are immutable after publication.

## Recording integration

Recording revisions pin library versions through `recording_revision_libraries`, described in [Recording PostgreSQL Schema](Recording_PostgreSQL_Schema.md). Annotation state rows reference `annotation_library_entries.id` directly.

The application service enforces that:

- Every referenced entry belongs to a library version pinned by the recording revision.
- Geometry type is permitted by the entry.
- Occurrence attributes validate against the entry's JSON Schema.

## Immutability

Ordinary application behavior never updates or deletes published library versions or entries. Database triggers or repository rules should reject those operations. Old versions cannot be deleted while a recording revision references them.
