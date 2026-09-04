# RecoveryHandler API

**Status:** Implemented

[Application API overview](../../Alignment_Workbench_Unified_Application_API.md)


```python
class RecoveryHandler(Protocol):
    def list_pending(self) -> tuple[PendingSave, ...]: ...

    def list_conflicts(self) -> tuple[RecoveryConflict, ...]: ...

    def retry(self, operation_id: UUID) -> RecoveryResult: ...

    def retry_all(self) -> tuple[RecoveryResult, ...]: ...

    def archive(self, operation_id: UUID) -> None: ...
```

Rules:

- Recovery envelopes contain complete deterministic recording creation or save requests.
- Every operation and proposed revision has a stable UUID.
- Recovery writes use temporary-file creation, flush, and atomic replacement.
- Retries are idempotent.
- A revision already committed with the same identity and compatible content is treated as applied.
- A changed remote head becomes a conflict.
- Malformed envelopes are quarantined.
- `archive` preserves the file in an abandoned/archive area; it does not permanently delete it.
- `retry_all` processes dependent operations in deterministic order and stops a recording's chain after its first conflict.
