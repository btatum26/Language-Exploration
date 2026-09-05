# Alignment Workbench Unified Application API

**Status:** Implemented

**Implementation:** `src/application/` provides validated runtime settings, the process lifecycle, public handlers, discovery DTOs, edit sessions, shared types, validation, local audio storage, recovery outbox, and the API composition root. `PersistenceStore` remains the injected database boundary.

**Scope:** Data models, data handling, recording editing, persistence coordination, local audio, and recovery

**Excluded:** GUI behavior, CLI presentation, playback controls, signal analysis algorithms, automatic annotation algorithms, and web/HTTP transport

## Documentation structure

The documentation follows the system boundary from public application contracts through handlers and edit sessions to injected infrastructure ports. Recovery and testing remain separate because they coordinate or verify the other parts rather than belonging to one recording operation.

- [Shared application contracts](shared/Shared_Application_Contracts.md) define the entry points, discovery DTOs, common types, versioning, validation, errors, and lifecycle.
- [Application API usage](Usage.md) shows the editing and external-analysis flow.
- Public handlers expose application behavior:
  - Recordings
    - [RecordingHandler API](handlers/recording/Recording_Handler_API.md) covers the recording catalog, creation, opening, and revision access.
    - [RecordingEditSession API](handlers/recording/Recording_Edit_Session_API.md) covers in-memory editing, library use, history, saving, and the external-analysis boundary.
  - [LibraryHandler API](handlers/libraries/Library_Handler_API.md) covers annotation-library creation and immutable version publication.
  - [SpeakerHandler API](handlers/speakers/Speaker_Handler_API.md) covers the append-only speaker registry.
  - [RecoveryHandler API](handlers/recovery/Recovery_Handler_API.md) covers durable pending saves, retry, conflict handling, and archival.
- Internal ports connect handlers to infrastructure:
  - [AudioStorageHandler API](ports/Audio_Storage_Handler_API.md) defines immutable local audio ingestion, resolution, and verification.
  - [PersistenceStore API](ports/Persistence_Port_API.md) defines the database boundary used by the application handlers.
- [Application API testing](testing/Application_API_Testing.md) maps the implemented handler and edit-session contract coverage.

## Related documentation

- [System architecture](../architecture.md)
- [Signal annotation data model](../data-models/Language_Exploration_Signal_Annotation_Data_Model.md)
- [Application API usage](Usage.md)
- [Persistence architecture](../persistence/Persistence_Architecture.md)
