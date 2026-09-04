# LibraryHandler API

**Status:** Implemented

[Application API overview](../../Alignment_Workbench_Unified_Application_API.md)


`LibraryHandler` manages reusable annotation definitions. Recording sessions use published versions but do not publish them.

```python
class LibraryHandler(Protocol):
    def list(self) -> tuple[Library, ...]: ...

    def get(self, library_id: UUID) -> Library: ...

    def create(self, library: Library) -> Library: ...

    def list_versions(
        self,
        library_id: UUID,
    ) -> tuple[LibraryVersion, ...]: ...

    def get_version(
        self,
        library_version_id: UUID,
    ) -> LibraryVersion: ...

    def find_version(
        self,
        namespace: str,
        version: str,
    ) -> LibraryVersion: ...

    def publish_version(
        self,
        version: LibraryVersion,
    ) -> LibraryVersion: ...
```

Rules:

- Library namespaces are globally unique.
- Library versions are immutable.
- Entry order is preserved.
- Entry keys are unique within a version.
- Version labels and content hashes are unique within a library.
- `publish_version` verifies the deterministic content hash. Callers may use the exported `library_content_sha256` helper while constructing a version.
- No update or delete methods are exposed in the initial API.

The handler may use the existing `LibraryStore` persistence protocol. `RecordingEditSession.pin_library_version` receives complete published versions from this handler.
