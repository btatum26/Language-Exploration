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
    persistence.py   framework-free store protocols
    read_models.py   frozen query projections
    errors.py        stable persistence exceptions

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
| Persistence protocols | Public storage operations and return types | SQLAlchemy implementation details |
| `SqlAlchemyPersistence` | Session lifecycle, transactions, repositories, error translation | GUI state, audio bytes, recovery files |
| Repositories and mappers | Bounded queries, inserts, exact hydration | Commits or public framework types |
| PostgreSQL | Referential integrity, uniqueness, basic shape checks, linear history | JSON Schema evaluation, audio files |
| Audio resolver protocol | Logical URI to path boundary | Ingestion, copying, decoding, playback |

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
- JSON Schema policy, Nyquist checks, edit commands, and recovery coordination belong to the proposed application layer.

## Future integration points

The following are documented contracts but are not implemented in this checkout:

- `WorkbenchAPI` and its handlers;
- `RecordingEditSession` and undo/redo state;
- immutable audio ingestion and a concrete storage resolver;
- the durable recovery outbox and retry coordinator;
- GUI and CLI integration.

Their target contracts are in the [application API documentation](../application-api/Alignment_Workbench_Unified_Application_API.md).

## Exclusions

The persistence layer has no semantic annotation relations, hierarchy, branches, merges, diff replay, analysis jobs, playback, GUI types, or audio-byte storage.
