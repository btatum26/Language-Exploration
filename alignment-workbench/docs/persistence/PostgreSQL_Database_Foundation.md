# PostgreSQL Database Foundation

**Status:** Implemented

The immutable recording-snapshot database is owned by `alignment-workbench`. The foundation
contains typed SQLAlchemy rows, engine/session construction, Alembic migrations, and database
tests. The model-facing repositories and hydration layer built on this schema are
documented in [SQLAlchemy Persistence Layer](SQLAlchemy_Persistence_Layer.md). Audio transfer,
storage operations, and GUI database access remain outside the persistence package.

## Executable definitions

- `alembic.ini` and `alembic/env.py` configure migrations.
- `alembic/versions/` contains the executable schema history.
- `src/persistence/sqlalchemy/rows.py` mirrors the installed tables for application access.
- `sql/configure_runtime_role.sql` owns environment-specific runtime grants.

The nine installed tables and their integrity rules are documented once in
[PostgreSQL Schema](PostgreSQL_Schema.md).

The pre-snapshot remote schema was retained at the 2026-08-30 cutover as
`registry_align_legacy_20260830`. It remains outside the current schema and requires a separate,
explicit migration decision.

## Configuration

Copy `.env.example` to `.env` and set both URLs. Runtime sessions use
`REGISTRY_ALIGN_DATABASE_URL`; Alembic prefers `REGISTRY_ALIGN_DATABASE_ADMIN_URL`. Both URLs name
the local endpoint forwarded by the `registry-db` SSH alias. Credentials must not be placed in
source, committed configuration, commands, or logs.

The application composition root, online Alembic environment, and explicit PostgreSQL test session
each start and stop their own hidden OpenSSH process. The SQLAlchemy persistence package does not
own subprocesses. Do not launch a tunnel separately; the configured local port must be free when
one of these entry points starts. The alias, executable, and readiness timeout have optional
`.env` overrides documented in `.env.example`.

## Migration commands

From `alignment-workbench`:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run alembic -c .\alembic.ini current
uv run alembic -c .\alembic.ini upgrade head
```

Online Alembic commands own the SSH tunnel for the duration of the command. Offline SQL generation
does not contact PostgreSQL and does not start SSH.

Migration-cycle tests perform downgrade and re-upgrade only against an explicit disposable target.
Never run `downgrade base` against the remote `registry_align` schema merely to test the cycle.

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

## Verification

Database tests require an explicitly disposable database or schema and never fall back to
`REGISTRY_ALIGN_DATABASE_URL`. Current commands, environment variables, coverage, and safety
rules are maintained in [Data Model and Persistence Testing](../testing/Data_Model_and_Persistence_Testing.md).
