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
    ) -> tuple[RecordingSummary, ...]: ...

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

Returns lightweight summaries and never loads complete annotation collections.

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

If PostgreSQL rejects the recording for a semantic or integrity reason, creation fails and the unreferenced audio file is retained for explicit reconciliation rather than silently deleted.

## `open`

Opening a recording:

1. Checks the recovery outbox for a pending or conflicted operation for the recording.
2. Raises `PendingRecoveryOperationError` without loading PostgreSQL when local recovery state exists.
3. Loads the current `RecordingWorkspace` from persistence.
4. Resolves the audio URI through the audio-storage handler.
5. Builds an in-memory edit session.
6. Closes the database transaction before returning.

This first implementation does not reconstruct a session from recovery files after a restart. The recovery operation must be retried or archived before the recording can be opened.

## Revision access

`load_revision` returns immutable historical data. It does not move the current head. Restoration happens through an already-open edit session so the session can preserve the current head as the expected parent.
