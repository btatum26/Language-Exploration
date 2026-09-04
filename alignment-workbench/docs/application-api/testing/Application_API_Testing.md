# Application API Testing

**Status:** Implemented contract coverage

[Application API overview](../Alignment_Workbench_Unified_Application_API.md)

## Contract testing requirements

The application API is tested against a contract-faithful in-memory persistence port and real temporary audio and recovery directories in `tests/test_application_api.py`.

The contract tests cover:

1. Create, open, annotate, save, and reopen a recording.
2. Opened sessions contain no ORM rows or database sessions.
3. Recording mutations correctly set and clear `dirty`.
4. Metadata and annotation mutations undo and redo deterministically.
5. Batch additions are atomic.
6. Annotation order remains stable.
7. Concepts resolve only through pinned versions.
8. Disallowed geometry is rejected at the affected mutation.
9. A referenced library version cannot be unpinned.
10. Historical restoration becomes a new revision on save.
11. Audio ingestion uses atomic publication and stable URIs.
12. Missing audio produces a typed application error.
13. Database unavailability produces `Queued` and a durable recovery envelope.
14. An ambiguous commit retry recognizes an already-applied stable revision.
15. A changed remote head produces `SaveConflict` without losing local edits.
16. External annotations can be added without an analysis-specific interface.
17. No ordinary in-memory edit performs database I/O.

Implemented lower-level coverage is documented in [Data model and persistence testing](../../testing/Data_Model_and_Persistence_Testing.md).
