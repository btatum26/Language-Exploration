# Data Model and Persistence Testing

**Status:** Implemented coverage for the domain, application, and persistence layers

**Scope:** Tests present in this checkout

## Test suites

| Suite | Coverage |
| --- | --- |
| `tests/test_domain_models.py` | Strict immutable models, deterministic JSON, geometry, snapshots, and stable create/save revision IDs |
| `tests/test_persistence_mappers.py` | Domain-to-row values and trusted row-to-domain hydration |
| `tests/test_application_api.py` | Public handlers, edit sessions, validation, WAV storage, durable recovery, retries, and conflicts |
| `tests/database/test_snapshot_metadata.py` | ORM metadata, constraints, indexes, engine, and session configuration |
| `tests/database/test_snapshot_postgresql.py` | Alembic lifecycle and PostgreSQL constraint behavior |
| `tests/database/test_persistence_postgresql.py` | Store CRUD, bounded reads, ordered hydration, atomic creation/saves, retries, and real concurrent-save behavior |
| `tests/database/test_runtime_role_postgresql.py` | Effective runtime privileges and facade behavior as `registry_align_app` |

## Domain coverage

The unit suite covers:

- canonical lowercase SHA-256 and strict JSON values;
- concept-reference grammar and immutable nested JSON;
- point, interval, box, and polygon geometry;
- unique annotation IDs and pinned namespace-version pairs;
- audio-frame bounds and pinned concept membership;
- deterministic serialization;
- stable `initial_revision_id` and `new_revision_id` values across serialization.

Library-specific JSON Schema policy and Nyquist validation belong to the application layer rather than the domain models.

## Persistence coverage

The PostgreSQL persistence suite covers:

- audio, speaker, library, version, and ordered entry operations;
- current and historical snapshot reads;
- workspace hydration with complete pinned versions;
- three SELECTs without pins and at most four with pins;
- initial recording creation and complete immutable revision saves;
- stable-ID retry after an ambiguous commit;
- incompatible revision-ID reuse;
- stale-parent rejection;
- a synchronized two-connection race in which exactly one competing save succeeds;
- atomic rollback and typed error translation.

## Schema and runtime-role coverage

Disposable-database tests cover migration upgrade, downgrade, and re-upgrade; all nine tables; foreign keys; uniqueness; linear history; pinned version/entry integrity; geometry shapes; finite frequencies; confidence; and JSON-object constraints.

Runtime-role tests are separate and opt in through `TEST_RUNTIME_DATABASE_URL`. They verify the effective role, exact table and column grants, immutable write rejection, and the full persistence facade under deployment permissions.

## Application coverage

The handler, edit-session, audio-storage, and recovery contracts are covered in `tests/test_application_api.py`. The case map is in [Application API testing](../application-api/testing/Application_API_Testing.md).

Producer-specific abstractions remain excluded, and GUI behavior remains future integration work. Analysis-boundary expectations are documented in [Analysis notes](../data-models/Analysis_Notes.md).

## Commands

From `alignment-workbench`:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run pytest
```

PostgreSQL tests require an explicit disposable target:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://OWNER:PASSWORD@127.0.0.1:5433/registry_align'
$env:TEST_DATABASE_SCHEMA = 'registry_align_snapshot_test'
uv run pytest tests/database
```

Runtime-role verification requires the deployed application role explicitly:

```powershell
$env:TEST_RUNTIME_DATABASE_URL = 'postgresql+psycopg://registry_align_app:PASSWORD@127.0.0.1:5433/registry_align'
uv run pytest tests/database/test_runtime_role_postgresql.py
```

Never point migration-cycle or destructive persistence fixtures at a development or shared schema.
