# RecordingHandler API

**Status:** Implemented

[Application API overview](../../Alignment_Workbench_Unified_Application_API.md)


`RecordingHandler` manages the recording catalog and creates in-memory editing sessions.

```python
class RecordingHandler(Protocol):
    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]: ...

    def create(
        self,
        command: CreateRecordingCommand,
    ) -> RecordingEditSession: ...

    def open(
        self,
        recording_id: UUID,
    ) -> RecordingEditSession: ...

    def list_revisions(
        self,
        recording_id: UUID,
    ) -> tuple[RecordingRevisionSummary, ...]: ...

    def load_revision(
        self,
        recording_id: UUID,
        revision_id: UUID,
    ) -> AnnotatedRecordingSnapshot: ...
```

## `list`

Returns frozen GUI-facing list items and never loads complete annotation collections. The query joins each current head, immutable audio metadata, and optional speaker in one bounded PostgreSQL read. Local storage resolution then reports `available`, `missing`, or `invalid` without hashing every audio file. Ordering is recording name then recording ID.

## `create`

Creation performs this coordinated workflow:

1. Inspect and hash the source audio.
2. Copy it to a temporary file under the configured audio root.
3. Atomically publish the immutable audio file.
4. Construct an `AudioAsset` with a stable `registry-audio://assets/<uuid>` URI.
5. Construct the complete initial persistence request with stable recording and revision IDs.
6. Persist the recovery envelope atomically.
7. Attempt the PostgreSQL creation transaction.
8. Return a `RecordingEditSession` representing either synchronized or locally pending state.

If validation, recovery-envelope creation, or PostgreSQL rejects the import before committing, the application removes the managed copy only when its bytes still match the imported asset. An unavailable or ambiguously committed database operation retains the copy with the existing durable recovery envelope. The selected source file is never changed or deleted.

## `open`

Opening a recording:

1. Checks the recovery outbox for a pending or conflicted operation for the recording.
2. Raises `PendingRecoveryOperationError` without loading PostgreSQL when local recovery state exists.
3. Loads the current `RecordingWorkspace` from persistence.
4. Resolves the audio URI and verifies the managed file's complete SHA-256 digest.
5. Builds an in-memory edit session.
6. Closes the database transaction before returning.

This first implementation does not reconstruct a session from recovery files after a restart. The recovery operation must be retried or archived before the recording can be opened.

## Closing

`RecordingEditSession.close()` is idempotent, releases local edit history, and never deletes managed audio. It rejects unsaved in-memory changes unless the caller explicitly requests discard. Saving before close continues to append a complete immutable revision through the existing optimistic-concurrency semantics. Closed sessions reject further operations.

## Revision access

`load_revision` returns immutable historical data. It does not move the current head. Restoration happens through an already-open edit session so the session can preserve the current head as the expected parent.
