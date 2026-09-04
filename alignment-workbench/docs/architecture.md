# System Architecture

**Status:** Domain and persistence implemented; workbench application layer proposed

## Current system boundary

```text
Pydantic domain and request models
              |
              v
Application persistence protocols, read models, and errors
              |
              v
SqlAlchemyPersistence
              |
              v
PostgreSQL registry_align schema
```

The current checkout contains the portable model and synchronous persistence foundation. It does not contain a Qt GUI, playback engine, editing session, analysis runner, audio-ingestion service, or recovery outbox.

`AudioResolver` is the only current audio-service boundary. It resolves a logical storage URI to a local path; no concrete resolver or ingestion implementation is present.

## Proposed application boundary

The [application API](application-api/Alignment_Workbench_Unified_Application_API.md) defines the
next layer and owns its detailed component map. Its handler, session, audio-storage, and recovery
contracts are proposals. The implemented `PersistenceStore` protocols are the stable lower
boundary they will call.

## Dependency rules

- Domain models in `src/models.py` do not import persistence or framework types.
- Public persistence protocols, read models, and errors in `src/application/` do not expose SQLAlchemy.
- SQLAlchemy rows, sessions, expressions, and PostgreSQL types remain inside `src/persistence/sqlalchemy/`.
- Each public persistence call owns one short-lived session and transaction.
- PostgreSQL stores metadata and immutable snapshots, not audio bytes.
- Analysis systems produce ordinary `SignalAnnotation` values and do not write SQL directly.
- GUI, CLI, audio, recovery, and analysis code must depend inward through application contracts.

## Data and history

PostgreSQL is authoritative for recording revisions and published library versions. A recording points to one immutable audio asset and one current head revision. Each save appends a complete snapshot and advances the head with optimistic compare-and-swap semantics.

Audio identity and metadata are stored in PostgreSQL through `AudioAsset`, while bytes remain outside the database. The canonical locator is `registry-audio://assets/<uuid>`.

Edit-session undo history and pending recovery operations are not canonical PostgreSQL history. They belong to the proposed application layer.

## Documentation map

- [Data model](data-models/Language_Exploration_Signal_Annotation_Data_Model.md)
- [Persistence architecture](persistence/Persistence_Architecture.md)
- [PostgreSQL schema](persistence/PostgreSQL_Schema.md)
- [SQLAlchemy persistence layer](persistence/SQLAlchemy_Persistence_Layer.md)
- [Application API](application-api/Alignment_Workbench_Unified_Application_API.md)
- [Current test coverage](testing/Data_Model_and_Persistence_Testing.md)
