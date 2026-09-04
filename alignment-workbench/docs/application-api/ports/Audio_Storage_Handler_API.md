# Internal AudioStorageHandler port

**Status:** Proposed; only the narrower `AudioResolver.resolve(storage_uri) -> Path` protocol exists

[Application API overview](../Alignment_Workbench_Unified_Application_API.md)


```python
class AudioStorageHandler(Protocol):
    def ingest(
        self,
        source_path: Path,
        *,
        asset_id: UUID,
    ) -> AudioAsset: ...

    def resolve(self, audio_asset: AudioAsset) -> ResolvedAudio: ...

    def verify(self, audio_asset: AudioAsset) -> AudioVerificationResult: ...
```

## Ingestion

Ingestion:

1. Rejects nonexistent, unsupported, or unreadable sources.
2. Computes SHA-256 while reading.
3. Extracts the authoritative sample rate, frame count, channel count, media type, extension, and codec.
4. Writes a temporary file below the configured audio root.
5. Flushes the completed file.
6. Atomically renames it to the immutable final location.
7. Returns an `AudioAsset` with a logical URI.

## Resolution

Resolution:

- accepts only the configured logical URI scheme;
- validates the asset UUID encoded by the URI;
- prevents path traversal;
- confirms the resolved target is a regular file under the configured root;
- does not hash the entire file during every normal open.

Full hashing belongs to ingestion and explicit `verify` or maintenance audits.

## Immutability

The handler does not expose in-place audio mutation. Transforming audio creates a new asset and normally a new recording. Temporary or provably unreferenced files may be reconciled by an explicit maintenance operation outside ordinary editing.
