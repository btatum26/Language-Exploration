# Alignment Workbench PostgreSQL Database Historical Specification

**Status:** Implemented historical specification  
**Owner:** `alignment-workbench`

This file records the specification used to build the initial database foundation. It is not an
active implementation prompt. All database migrations, SQLAlchemy mappings, database tests, and
runtime-role policy described here belong to `alignment-workbench`, not `registry-aligner`.

---

The PostgreSQL database foundation belongs to the `alignment-workbench` subproject in the Language
Exploration repository.

This task is deliberately limited to the database foundation. Do **not** implement the application engine, repository use cases, GUI, audio-transfer code, or local recovery runtime yet.

## Required first steps

1. Inspect the repository and read every applicable `AGENTS.md` file.
2. Locate and review the current Pydantic domain models for immutable annotated recording snapshots. Treat those models and the requirements below as the source of truth.
3. Inspect the existing dependency and package configuration before changing it.
4. Inspect the current PostgreSQL/Alembic/SQLAlchemy state, if any, and preserve unrelated work.
5. Report any direct contradiction between the repository and these requirements before making an irreversible design choice. Do not revive legacy behavior merely because old code exists.

## Binding project constraints

- This is a clean rebuild. There is no backward compatibility requirement.
- Do not migrate existing SQLite data or disposable test runs.
- Do not maintain a SQLite production implementation.
- PostgreSQL is the authoritative metadata and annotation database.
- Target PostgreSQL 16+ using SQLAlchemy 2.x, Psycopg 3, Alembic, and Pydantic 2.
- Use synchronous SQLAlchemy.
- Use UUID primary keys, JSONB, timezone-aware timestamps, foreign keys, checks, and explicit indexes.
- Use `pgcrypto` for `gen_random_uuid()` defaults where database-generated IDs are useful.
- Do not use PostgreSQL enum types. Use checked text.
- Put application tables in PostgreSQL schema `registry_align`.
- Use a deterministic SQLAlchemy naming convention for every constraint and index.
- Audio bytes are stored locally on the server filesystem, never in PostgreSQL.
- Each recording references exactly one immutable audio asset.
- Identical audio content may be deduplicated into one audio asset while distinct logical recordings remain separate.
- Store a stable logical `storage_uri` and SHA-256 for the server audio file.
- SQLAlchemy does not read, upload, download, decode, or copy audio in this task.
- Recording history is linear and consists of complete immutable snapshots.
- There are no branches, merges, diffs, event sourcing, semantic annotation relations, or annotation hierarchy.
- Do not create a relation table or source/target annotation columns.
- Do not modify the separate spectrogram application.

## Current server configuration

- Database name: `registry_align`.
- Owner/migration role: `registry_align_owner`.
- Runtime role: `registry_align_app`.
- The PostgreSQL server listens on loopback only.
- Local development normally connects through an SSH tunnel at `127.0.0.1:5433` forwarding to server `127.0.0.1:5432`.
- Read the SQLAlchemy URL from `REGISTRY_ALIGN_DATABASE_URL`.
- Never hardcode credentials.
- Do not create or modify PostgreSQL roles in a portable Alembic revision.
- Tests must never silently fall back to the production database URL.

## Scope to implement now

Implement only:

1. SQLAlchemy declarative base and metadata.
2. SQLAlchemy ORM row classes for the schema below.
3. Engine and session-factory configuration.
4. Alembic configuration and a clean baseline migration that creates the new schema from an empty PostgreSQL database.
5. Database-focused tests for table shape, constraints, indexes, and clean migration behavior.
6. Minimal configuration documentation and an `.env.example` entry for `REGISTRY_ALIGN_DATABASE_URL` if the repository's conventions call for it.

Do not implement:

- Repository protocols or repository methods.
- Pydantic-to-ORM or ORM-to-Pydantic mappers.
- Snapshot save/load services.
- Business validation services.
- Trusted `model_construct()` hydration.
- Audio storage adapters or server file operations.
- Local recovery outbox behavior.
- Qt or any GUI work.
- MFA, alignment, spectrogram, or analysis behavior.
- Importers, seed libraries, API endpoints, or CLI workflows beyond an existing migration command pattern.
- Legacy schema/data conversion.

## Suggested package structure

Adapt names to the existing repository conventions rather than duplicating an established package:

```text
src
    persistence/
        sqlalchemy/
            base.py
            rows.py
            session.py
    domain/
        models.py                 # existing; do not turn into ORM models
alembic/
    env.py
    versions/
tests/
    database/
```

The Pydantic domain models and SQLAlchemy row classes must remain separate.

## SQLAlchemy base and engine

Use SQLAlchemy 2.x typed declarative mappings with `Mapped[...]` and `mapped_column()`.

Configure metadata with schema `registry_align` and this naming convention, or an equivalent convention already established in the repository:

```python
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

Provide an engine factory rather than opening a connection at import time. Use the `postgresql+psycopg` dialect. The default runtime profile should be equivalent to:

```python
create_engine(
    database_url,
    pool_size=2,
    max_overflow=3,
    pool_pre_ping=True,
)
```

Provide a session-factory builder using `expire_on_commit=False`, `autoflush=False`, and ordinary transactional sessions. Do not create a global live `Session`. The engine may be shared; sessions may not be shared across threads.

Do not open the SSH tunnel in the SQLAlchemy module. The application composition root establishes
and owns the tunnel before constructing SQLAlchemy persistence.

## Tables to implement

Create the following tables in schema `registry_align`.

### `audio_assets`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `storage_uri TEXT NOT NULL UNIQUE CHECK (length(storage_uri) > 0)`
- `sha256 TEXT NOT NULL UNIQUE`
- `logical_path TEXT NULL`
- `media_type TEXT NULL`
- `original_extension TEXT NULL`
- `codec TEXT NULL`
- `sample_rate_hz INTEGER NOT NULL CHECK > 0`
- `frame_count BIGINT NOT NULL CHECK > 0`
- `channels INTEGER NOT NULL CHECK > 0`
- `source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Checks:

- `sha256` is lowercase and matches exactly 64 hexadecimal characters.
- `source_metadata` is a JSON object.

The later application contract standardized this field as `storage_uri`; `logical_path` remains
optional provenance or display information.

The authoritative value is a logical URI of the form
`registry-audio://assets/<audio-asset-uuid>`. Relative server-storage keys are not part of the
application contract. PostgreSQL enforces nonemptiness; the future service layer will validate the
exact URI scheme and UUID form.

### `speakers`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `external_key TEXT NULL`
- `display_name TEXT NOT NULL` with a nonempty check matching the current domain behavior
- `metadata JSONB NOT NULL DEFAULT '{}'::jsonb`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Create a partial unique index on non-null `external_key`. Check that `metadata` is a JSON object.

### `annotation_libraries`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `namespace TEXT NOT NULL UNIQUE`
- `name TEXT NOT NULL`
- `description TEXT NULL`
- `owner_label TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Match the namespace and nonempty-name constraints used by the current Pydantic model.

### `library_versions`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `library_id UUID NOT NULL`
- `version_label TEXT NOT NULL`
- `content_sha256 TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `author TEXT NULL`
- `description TEXT NULL`

Constraints and indexes:

- FK `library_id -> annotation_libraries.id`, `ON DELETE RESTRICT`.
- Unique `(library_id, version_label)`.
- Unique `(library_id, content_sha256)`.
- Check version-label format against the domain model.
- Check lowercase SHA-256 format.
- Index `(library_id, created_at DESC)`.

### `library_entries`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `library_version_id UUID NOT NULL`
- `position INTEGER NOT NULL CHECK >= 0`
- `entry_key TEXT NOT NULL`
- `display_name TEXT NOT NULL`
- `description TEXT NOT NULL`
- `allowed_geometry_types TEXT[] NOT NULL`
- `attribute_schema JSONB NOT NULL DEFAULT '{"type":"object"}'::jsonb`
- `validation_hints JSONB NOT NULL DEFAULT '{}'::jsonb`
- `display_hints JSONB NOT NULL DEFAULT '{}'::jsonb`
- `metadata JSONB NOT NULL DEFAULT '{}'::jsonb`

Constraints:

- FK to `library_versions`, `ON DELETE RESTRICT`.
- Unique `(library_version_id, entry_key)`.
- Unique `(library_version_id, position)`.
- Unique `(id, library_version_id)` for a later composite annotation FK.
- `entry_key` format matches the domain model.
- `display_name` is nonempty.
- `allowed_geometry_types` has at least one item and contains only `point`, `time_interval`, `time_frequency_box`, or `time_frequency_polygon`.
- All four JSONB document columns contain JSON objects.

Do not execute JSON Schema validation inside PostgreSQL.

### `recordings`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `audio_asset_id UUID NOT NULL`
- `head_revision_id UUID NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

Constraints and indexes:

- FK `audio_asset_id -> audio_assets.id`, `ON DELETE RESTRICT`.
- Index `(audio_asset_id)`.
- Do **not** make `audio_asset_id` unique. Distinct logical recordings may share deduplicated identical audio, while each recording still references exactly one audio asset.
- Add the composite head-revision FK after `recording_revisions` exists: `(id, head_revision_id) -> recording_revisions(recording_id, id)`.

The `audio_asset_id` and `created_at` columns are immutable. `head_revision_id` is the only routinely updated column.

### `recording_revisions`

- `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
- `recording_id UUID NOT NULL`
- `revision_number INTEGER NOT NULL CHECK > 0`
- `parent_revision_id UUID NULL`
- `schema_version TEXT NOT NULL DEFAULT '1.0'`
- `name TEXT NOT NULL`
- `default_speaker_ref UUID NULL`
- `language TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `author TEXT NULL`
- `message TEXT NULL`

Constraints and indexes:

- FK `recording_id -> recordings.id`, `ON DELETE RESTRICT`.
- FK `default_speaker_ref -> speakers.id`, `ON DELETE RESTRICT`.
- Unique `(recording_id, revision_number)`.
- Unique `(recording_id, id)` for composite references.
- Composite FK `(recording_id, parent_revision_id) -> recording_revisions(recording_id, id)`.
- Check: revision 1 has a null parent; revisions greater than 1 have a non-null parent.
- Partial unique index on non-null `parent_revision_id` to prevent revision branches.
- Check `schema_version = '1.0'` in the initial schema.
- Nonempty checks for `name` and `language` matching the current Pydantic behavior.
- Index `(recording_id, created_at DESC)`.

The domain model uses `default_speaker_ref: UUID | None`; persist it as a UUID foreign key to
`speakers.id`.

Handle the `recordings`/`recording_revisions` foreign-key cycle explicitly in SQLAlchemy metadata and Alembic, for example with a named `use_alter` constraint where appropriate. Do not weaken the same-recording head guarantee.

### `recording_revision_libraries`

- `recording_revision_id UUID NOT NULL`
- `library_version_id UUID NOT NULL`
- `position INTEGER NOT NULL CHECK >= 0`

Constraints and indexes:

- Primary key `(recording_revision_id, library_version_id)`.
- FK to `recording_revisions`, `ON DELETE RESTRICT`.
- FK to `library_versions`, `ON DELETE RESTRICT`.
- Unique `(recording_revision_id, position)`.
- Index `(library_version_id)`.

### `annotations`

- `recording_revision_id UUID NOT NULL`
- `annotation_id UUID NOT NULL`
- `position INTEGER NOT NULL CHECK >= 0`
- `library_version_id UUID NOT NULL`
- `library_entry_id UUID NOT NULL`
- `geometry_type TEXT NOT NULL`
- `start_sample BIGINT NOT NULL`
- `end_sample BIGINT NULL`
- `min_frequency_hz DOUBLE PRECISION NULL`
- `max_frequency_hz DOUBLE PRECISION NULL`
- `polygon_vertices JSONB NULL`
- `attributes JSONB NOT NULL DEFAULT '{}'::jsonb`
- `confidence DOUBLE PRECISION NULL`
- `note TEXT NULL`
- `provenance_ref TEXT NULL`

Constraints and indexes:

- Primary key `(recording_revision_id, annotation_id)`.
- Unique `(recording_revision_id, position)`.
- Composite FK `(recording_revision_id, library_version_id) -> recording_revision_libraries(recording_revision_id, library_version_id)`, `ON DELETE RESTRICT`.
- Composite FK `(library_entry_id, library_version_id) -> library_entries(id, library_version_id)`, `ON DELETE RESTRICT`.
- `geometry_type` is checked text limited to the four domain values.
- `start_sample >= 0`.
- `confidence IS NULL OR confidence BETWEEN 0 AND 1`.
- `attributes` is a JSON object.
- Index `(recording_revision_id, start_sample)`.
- Index `(library_entry_id, recording_revision_id)`.

Add a named geometry-shape CHECK constraint that enforces:

- `point`: `end_sample`, both frequency columns, and `polygon_vertices` are null.
- `time_interval`: `end_sample > start_sample`; frequency columns and vertices are null.
- `time_frequency_box`: `end_sample > start_sample`; finite `min_frequency_hz >= 0`; finite `max_frequency_hz > min_frequency_hz`; vertices are null.
- `time_frequency_polygon`: the same scalar bounds plus `polygon_vertices` as a JSON array of at least three elements.

Use explicit finite-number checks compatible with PostgreSQL double precision so `NaN`, positive infinity, and negative infinity cannot satisfy frequency constraints.

Do not attempt cross-table checks against `audio_assets.frame_count`, per-vertex polygon validation, allowed-geometry lookup against a library entry, or JSON Schema validation inside PostgreSQL. Those belong to a later service boundary.

## ORM behavior

- Give row classes a clear suffix such as `Row` to distinguish them from domain classes.
- Define relationships only when they clarify schema navigation.
- Set application-facing relationships to `lazy="raise"` to prevent accidental query storms.
- Do not rely on ORM cascade deletion for canonical history.
- Do not expose ORM rows as domain objects.
- Do not add business methods, validation services, or repository APIs to row classes.
- Use PostgreSQL-native `UUID`, `JSONB`, `ARRAY(Text)`, and timezone-aware timestamp types.

## Alembic behavior

- Configure Alembic to use the same metadata and naming convention.
- The baseline migration creates the `registry_align` schema, enables `pgcrypto` when permitted, creates all tables, constraints, and indexes, and supports clean downgrade in dependency-safe order.
- This is a clean schema-creation migration. It must contain no imports from changing application model metadata at migration execution time and no legacy-data backfill.
- Keep migration code deterministic and self-contained.
- Do not create database roles or embed environment-specific passwords.
- If `pgcrypto` cannot be created by the configured migration role, fail with an actionable message rather than silently removing UUID defaults.

## Tests and verification

Use a real disposable PostgreSQL database supplied only through an explicit `TEST_DATABASE_URL`. Never use SQLite for these tests and never silently substitute `REGISTRY_ALIGN_DATABASE_URL`.

At minimum verify:

1. SQLAlchemy metadata contains exactly the intended schema objects.
2. The baseline migration upgrades an empty PostgreSQL database successfully.
3. Downgrade to base and a second upgrade both succeed.
4. All tables are created in schema `registry_align`.
5. Expected primary keys, unique constraints, checks, composite foreign keys, and indexes exist.
6. A minimal valid graph can be inserted: audio asset, recording, first revision, library, version, entry, pin, and annotation; the recording head can then be set.
7. Duplicate library namespace, version label, entry key, SHA-256, revision number, revision position, and annotation position are rejected.
8. A revision cannot point to a parent from another recording.
9. Two revisions cannot create branches from the same parent.
10. A recording cannot point its head at another recording's revision.
11. An annotation cannot reference an unpinned library version.
12. An annotation cannot pair an entry with the wrong library version.
13. Invalid geometry column combinations and invalid confidence values are rejected.
14. JSON object checks reject arrays/scalars in object fields.

If `TEST_DATABASE_URL` is absent, database integration tests must skip with a clear reason or fail according to the repository's established test policy. They must not attempt SSH or contact the production database.

Run the relevant formatter, linter, type checker, unit tests, and PostgreSQL integration tests supported by the repository. Inspect the generated SQL and use PostgreSQL catalog inspection where needed; do not claim constraints exist solely because ORM classes declare them.

## Deliverables

Return:

1. The files added or changed.
2. The final table and index inventory.
3. Any deliberate difference from this specification and why it was necessary.
4. Exact commands for installing dependencies, configuring the URL, running the baseline migration, downgrading/upgrading it, and running database tests.
5. Verification results, including which checks were actually run and which could not be run.
6. A concise list of the next-phase work that was intentionally excluded.

Do not proceed into repository or engine implementation after the database foundation passes its tests.

---
