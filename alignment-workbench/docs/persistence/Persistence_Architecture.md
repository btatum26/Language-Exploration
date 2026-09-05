# Persistence Architecture

**Status:** Implemented

**Scope:** Domain-facing PostgreSQL persistence owned by `alignment-workbench`

## Purpose

The persistence layer stores immutable annotated-recording snapshots, linear recording history, immutable annotation-library versions, speakers, and audio metadata. It returns domain models and frozen read models; SQLAlchemy objects never cross its public boundary.

## Component map

```text
src/models.py
    immutable domain models and create/save requests

src/application/
    runtime.py        validated settings and process lifecycle
    ssh_tunnel.py     bounded hidden OpenSSH process and readiness checks
    workbench.py      public API composition
    cli.py            executable lifecycle/import smoke path
    handlers.py       public application handlers
    edit_session.py   in-memory recording editing
    contracts.py      commands, results, and infrastructure ports
    validation.py     concepts, JSON Schema, geometry, and hashes
    audio_storage.py  immutable local WAV storage
    recovery.py       durable filesystem recovery outbox
    persistence.py    framework-free store protocols
    read_models.py    frozen persistence and GUI discovery projections
    errors.py         stable application exceptions

src/gui/
    app.py            Qt and real application lifecycle wiring
    controller.py     public API and edit-session orchestration
    main_window.py    navigation, playback, and editing widgets
    tasks.py          Qt worker boundary for synchronous calls
    waveform.py       bounded PCM envelope and annotation rendering

src/persistence/
    audio/resolver.py           logical URI resolution protocol
    sqlalchemy/engine.py        synchronous engine construction
    sqlalchemy/session.py       session factory
    sqlalchemy/rows.py          internal ORM mappings
    sqlalchemy/mappers.py       domain/row translation
    sqlalchemy/*_repository.py  focused persistence operations
    sqlalchemy/unit_of_work.py  public transaction-owning facade

alembic/
    executable PostgreSQL schema migrations
```

## Responsibility boundaries

| Component | Owns | Does not own |
| --- | --- | --- |
| Domain models | Portable structure, immutability, deterministic JSON, snapshot-local validation | SQL, transactions, external analysis |
| `WorkbenchApplication` | SSH tunnel, configured infrastructure startup, and ordered cleanup | SQL queries or ORM rows |
| Persistence protocols | Public storage operations and return types | SQLAlchemy implementation details |
| `SqlAlchemyPersistence` | Session lifecycle, transactions, repositories, error translation | GUI state, audio bytes, recovery files |
| Repositories and mappers | Bounded queries, inserts, exact hydration | Commits or public framework types |
| PostgreSQL | Referential integrity, uniqueness, basic shape checks, linear history | JSON Schema evaluation, audio files |
| Audio storage | Immutable PCM WAV ingestion, logical URI resolution, and verification | Playback or in-place mutation |
| Recovery outbox | Durable pending operations, conflicts, retry records, and archival | Canonical revision history |

## Data placement

PostgreSQL stores the nine tables described in [PostgreSQL schema](PostgreSQL_Schema.md). Audio bytes remain outside PostgreSQL; only immutable identity, hash, timebase metadata, and logical URI are stored.

The schema and runtime role make canonical history insert-only. The only ordinary update is the compare-and-swap advance of `recordings.head_revision_id`.

## Read and write behavior

The [SQLAlchemy persistence layer](SQLAlchemy_Persistence_Layer.md) is the canonical description of query counts, hydration, transaction ownership, idempotent request-owned revision IDs, and error translation.

The [database foundation](PostgreSQL_Database_Foundation.md) owns configuration, migration, runtime-role, and disposable-database test instructions.

## Trust boundaries

- External JSON and newly constructed requests receive normal Pydantic validation.
- Snapshot models enforce aggregate-local identity, pin, and audio-frame rules.
- Persistence resolves stored library versions and entries, verifies content hashes and permitted geometry, and relies on PostgreSQL for structural integrity.
- Trusted reads use one centralized hydration path.
- The application layer enforces JSON Schema policy, Nyquist and audio-frame bounds, edit commands, and recovery coordination.

## GUI integration

The first-pass [desktop reference client](../gui/Reference_Client.md) consumes the application API
without importing persistence internals. Full CLI presentation and external analysis integrations
remain unimplemented. The non-GUI lifecycle/discovery/import smoke remains available through
`python src/cli_main.py`.

## Exclusions

The persistence layer has no semantic annotation relations, hierarchy, branches, merges, diff replay, analysis jobs, playback, GUI types, or audio-byte storage.
