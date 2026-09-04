# Internal PersistenceStore port

**Status:** Implemented

[Application API overview](../Alignment_Workbench_Unified_Application_API.md)


The existing persistence protocols remain the database boundary used by the handlers:

- `RecordingStore`
- `AudioAssetStore`
- `LibraryStore`
- `SpeakerStore`
- combined `PersistenceStore`

The application handlers add workflow semantics around those low-level operations. Public GUI or CLI code must not call SQLAlchemy repositories directly.

The persistence layer remains responsible for:

- short-lived database sessions and transactions;
- bounded snapshot and workspace reads;
- domain-model hydration;
- immutable insert operations;
- optimistic head comparison;
- idempotent stable revision writes;
- persistence error translation.

The concrete behavior is documented in the [SQLAlchemy persistence layer](../../persistence/SQLAlchemy_Persistence_Layer.md).
