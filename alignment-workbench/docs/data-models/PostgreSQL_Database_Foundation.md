# PostgreSQL Database Foundation

The immutable recording-snapshot database is owned by `alignment-workbench`. This phase contains
only typed SQLAlchemy rows, engine/session construction, Alembic migrations, and database tests.
It does not contain repositories, snapshot mappers, transfer services, audio storage operations,
or GUI database access.

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

## Database tests

The tests never fall back to `REGISTRY_ALIGN_DATABASE_URL`. Supply either a disposable database
whose name ends in `_test`, or an isolated schema whose name ends in `_test`:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://OWNER:PASSWORD@127.0.0.1:5433/registry_align'
$env:TEST_DATABASE_SCHEMA = 'registry_align_snapshot_test'
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\database
```

The integration suite verifies upgrade/downgrade/upgrade behavior, catalog shape, a complete
insert graph, uniqueness, same-recording parent/head guarantees, linear history, library pins,
entry-version pairing, geometry shape and finite-frequency checks, confidence, and JSON object
constraints.
