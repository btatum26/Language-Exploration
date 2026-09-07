# Internal AudioStorageHandler port

**Status:** Implemented for uncompressed PCM WAV and MP3 files

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

    def discard(self, audio_asset: AudioAsset) -> None: ...
```

## Ingestion

Ingestion:

1. Rejects nonexistent, unsupported, or unreadable sources.
2. Computes SHA-256 while reading.
3. Extracts the authoritative sample rate, frame count, channel count, media type, extension, and codec.
4. Writes a temporary file below the configured audio root.
5. Flushes the completed file.
6. Atomically publishes it without overwriting an existing immutable target.
7. Returns an `AudioAsset` with a logical URI.

## Resolution

Resolution:

- accepts only the configured logical URI scheme;
- validates the asset UUID encoded by the URI;
- prevents path traversal;
- confirms the resolved target is a regular file under the configured root;
- does not hash the entire file during catalog discovery.

Full hashing belongs to ingestion, recording open, explicit `verify`, or maintenance audits.

`LocalAudioStorage` is the concrete implementation. The decoder boundary accepts uncompressed PCM WAV and MPEG Layer III input and reports `UnsupportedAudioError` for other formats, compressed WAV data, or unreadable audio. Ingestion preserves the original bytes; MP3 metadata and waveform decoding use SoundFile's bundled libsndfile runtime.
Both resolution and verification reject logical URIs or filesystem links that escape the configured storage root.

`discard` is limited to compensating a failed import. It is idempotent for a missing target and refuses to remove a path outside the root or bytes whose hash no longer matches the staged asset. Ordinary edit-session close and application shutdown never call it.

## Immutability

The handler does not expose in-place audio mutation. Transforming audio creates a new asset and normally a new recording. Temporary or provably unreferenced files may be reconciled by an explicit maintenance operation outside ordinary editing.
