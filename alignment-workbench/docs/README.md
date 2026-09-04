# Alignment Workbench Documentation

This directory separates implemented contracts from proposed application behavior. The source tree and Alembic migrations remain authoritative when documentation and code disagree.

## Current status

| Area | State | Documentation |
| --- | --- | --- |
| Immutable domain models and request models | Implemented | [Data model](data-models/Language_Exploration_Signal_Annotation_Data_Model.md) |
| PostgreSQL schema and runtime-role policy | Implemented | [Persistence architecture](persistence/Persistence_Architecture.md) |
| Synchronous SQLAlchemy persistence | Implemented | [SQLAlchemy persistence layer](persistence/SQLAlchemy_Persistence_Layer.md) |
| Workbench handlers and recording edit session | Proposed | [Application API](application-api/Alignment_Workbench_Unified_Application_API.md) |
| Audio resolution | Protocol implemented | [Audio storage port](application-api/ports/Audio_Storage_Handler_API.md) |
| Audio ingestion and immutable local storage | Proposed | [Audio storage port](application-api/ports/Audio_Storage_Handler_API.md) |
| Recovery outbox and retry handlers | Proposed | [Recovery handler](application-api/handlers/recovery/Recovery_Handler_API.md) |
| GUI, playback, and signal-analysis execution | Not implemented in this checkout | [System architecture](architecture.md) |

## Reading order

- Start with [System architecture](architecture.md) for component ownership and implementation status.
- Use the [Data model overview](data-models/Language_Exploration_Signal_Annotation_Data_Model.md) for the canonical snapshot and annotation concepts.
- Use the [Persistence architecture](persistence/Persistence_Architecture.md) for database boundaries, then follow its links to schema, deployment, and SQLAlchemy details.
- Use the [Application API](application-api/Alignment_Workbench_Unified_Application_API.md) for the proposed public workbench surface.
- Use [Data model and persistence testing](testing/Data_Model_and_Persistence_Testing.md) for current automated coverage.

## Documentation ownership

- Domain meaning belongs in `data-models/`.
- PostgreSQL and SQLAlchemy behavior belongs in `persistence/`.
- Public handler and edit-session contracts belong in `application-api/`.
- Test coverage belongs in `testing/`.
- Complete examples remain in dedicated files rather than being duplicated across prose documents.
