# System Architecture

**Status:** Runnable lifecycle, synchronous application API, local audio storage, recovery, and persistence implemented

## Current system boundary

```text
WorkbenchApplication
       |
       v
WorkbenchAPI and RecordingEditSession
              |
              v
Application commands, results, validation, and handlers
              |
              v
PersistenceStore   AudioStorageHandler   RecoveryOutbox
       |                    |                   |
       v                    v                   v
SqlAlchemyPersistence  Local WAV storage  Recovery files
              |
              v
PostgreSQL registry_align schema
```

`WorkbenchApplication` is the process composition root. Creating it is configuration-only. `start()` constructs the owned PostgreSQL engine/session factory, local audio storage, recovery outbox, persistence adapter, and one `WorkbenchAPI`; it then validates the database through harmless recording and library reads. `shutdown()` invalidates the exposed API, closes the persistence facade, and disposes the connection pool. Startup and shutdown are idempotent, and the application supports `with` lifecycle use.

The current checkout contains the portable model, synchronous application API, in-memory editing session, immutable WAV storage, durable recovery outbox, synchronous PostgreSQL persistence, and a small executable smoke interface. It does not contain a Qt GUI, playback engine, or analysis runner.

`LocalAudioStorage` ingests, resolves, and verifies PCM WAV assets below one configured root. It uses `registry-audio://assets/<uuid>` as the stable logical URI.

## Application boundary

The [application API](application-api/Alignment_Workbench_Unified_Application_API.md) defines the public synchronous boundary and owns its detailed component map. Its handlers and edit sessions call injected `PersistenceStore`, `AudioStorageHandler`, and `RecoveryOutbox` ports.

## Dependency rules

- Domain models in `src/models.py` do not import persistence or framework types.
- Public handlers, contracts, persistence protocols, read models, and errors in `src/application/` do not expose SQLAlchemy.
- SQLAlchemy rows, sessions, expressions, and PostgreSQL types remain inside `src/persistence/sqlalchemy/`.
- Each public persistence call owns one short-lived session and transaction.
- PostgreSQL stores metadata and immutable snapshots, not audio bytes.
- Analysis systems produce ordinary `SignalAnnotation` values and do not write SQL directly.
- GUI, CLI, and analysis code must depend inward through application contracts.

## Data and history

PostgreSQL is authoritative for recording revisions and published library versions. A recording points to one immutable audio asset and one current head revision. Each save appends a complete snapshot and advances the head with optimistic compare-and-swap semantics.

Audio identity and metadata are stored in PostgreSQL through `AudioAsset`, while bytes remain outside the database. The canonical locator is `registry-audio://assets/<uuid>`.

Edit-session undo history and pending recovery operations are not canonical PostgreSQL history. Undo history is memory-only; recovery envelopes are durable local operations awaiting an explicit outcome.

## Documentation map

- [Data model](data-models/Language_Exploration_Signal_Annotation_Data_Model.md)
- [Persistence architecture](persistence/Persistence_Architecture.md)
- [PostgreSQL schema](persistence/PostgreSQL_Schema.md)
- [SQLAlchemy persistence layer](persistence/SQLAlchemy_Persistence_Layer.md)
- [Application API](application-api/Alignment_Workbench_Unified_Application_API.md)
- [Current test coverage](testing/Data_Model_and_Persistence_Testing.md)
