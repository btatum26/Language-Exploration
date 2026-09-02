# PostgreSQL Database Foundation

The immutable recording-snapshot database is owned by `alignment-workbench`. The foundation
contains typed SQLAlchemy rows, engine/session construction, Alembic migrations, and database
tests. The model-facing repositories and hydration layer built on this unchanged schema are
documented in [SQLAlchemy Persistence Layer](SQLAlchemy_Persistence_Layer.md). Audio transfer,
storage operations, and GUI database access remain outside the persistence package.

## Layout

```text
alignment-workbench/
    alembic.ini
    alembic/
        env.py
        versions/
    src/persistence/sqlalchemy/
        base.py
        rows.py
        session.py
    tests/database/
```

The baseline creates these tables in PostgreSQL schema `registry_align`:

```text
annotation_libraries       library_versions              library_entries
audio_assets               speakers                      recordings
recording_revisions        recording_revision_libraries  annotations
```

The previous remote schema is retained intact as `registry_align_legacy_20260830`. Its 177
recordings, 177 audio assets, 177 alignment results, 5,798 segments, and supporting run and
transcript rows remain available for a later explicit migration.

## Audio URI contract

`audio_assets.storage_uri` is a nonempty, unique logical identifier. The authoritative form is:

```text
registry-audio://assets/<audio-asset-uuid>
```

Relative server-storage keys are not part of the application contract. PostgreSQL deliberately
enforces only nonemptiness and uniqueness; the future audio service must validate the exact scheme
and UUID form before writing a row. This keeps physical storage and transport choices out of the
database while ensuring the workstation never receives a server-relative path.

## Configuration

Copy `.env.example` to `.env` and set both URLs. Runtime sessions use
`REGISTRY_ALIGN_DATABASE_URL`; Alembic prefers `REGISTRY_ALIGN_DATABASE_ADMIN_URL`. Credentials
must not be placed in source, committed configuration, commands, or logs.

The persistence package never opens the SSH tunnel. Start it separately:

```powershell
ssh -N registry-db
```

## Migration commands

From `alignment-workbench`:

```powershell
.\.venv\Scripts\python.exe -m alembic -c .\alembic.ini current
.\.venv\Scripts\python.exe -m alembic -c .\alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c .\alembic.ini downgrade base
.\.venv\Scripts\python.exe -m alembic -c .\alembic.ini upgrade head
```

Do not run `downgrade base` against the remote `registry_align` schema merely to test the cycle.
Use an explicit disposable target.

## Runtime role

Alembic does not create roles or apply deployment-specific grants. After migrations, run
[`sql/configure_runtime_role.sql`](../../sql/configure_runtime_role.sql) as
`registry_align_owner`. The policy is:

- `USAGE` on `registry_align`.
- `SELECT` and `INSERT` on all nine application tables.
- Column-level `UPDATE` only on `recordings.head_revision_id`.
- No table-level `UPDATE`, `DELETE`, or `TRUNCATE` on application tables.
- `SELECT` only on `public.registry_align_snapshot_alembic_version` so startup can verify the
  deployed revision.
- Owner default privileges grant only `SELECT` and `INSERT` on future tables and no sequence
  privileges.

The SQL file first revokes earlier broad grants, so it is safe to rerun after a migration. It must
be executed as the object owner because PostgreSQL default privileges belong to the role that
creates future objects.

## Database tests

The tests never fall back to `REGISTRY_ALIGN_DATABASE_URL`. Supply either a disposable database
whose name ends in `_test`, or an isolated schema whose name ends in `_test`:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://OWNER:PASSWORD@127.0.0.1:5433/registry_align'
$env:TEST_DATABASE_SCHEMA = 'registry_align_snapshot_test'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\database
```

To verify the deployed runtime role itself, provide its URL explicitly. This test inserts a full
snapshot graph inside a rolled-back transaction, advances only the recording head, and confirms
that immutable updates and deletes receive PostgreSQL permission errors:

```powershell
$env:TEST_RUNTIME_DATABASE_URL = 'postgresql+psycopg://registry_align_app:PASSWORD@127.0.0.1:5433/registry_align'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\database\test_runtime_role_postgresql.py
```

The integration suite verifies upgrade/downgrade/upgrade behavior, catalog shape, a complete
insert graph, uniqueness, same-recording parent/head guarantees, linear history, library pins,
entry-version pairing, geometry shape and finite-frequency checks, confidence, and JSON object
constraints. Explicit rejection cases cover empty storage URIs, empty or invalid allowed-geometry
arrays, invalid speaker references, duplicate library version labels and hashes, duplicate pinned
positions, and NaN or positive/negative infinity in either frequency column.
