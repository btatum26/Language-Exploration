# SpeakerHandler API

**Status:** Proposed; the lower-level `SpeakerStore` is implemented

[Application API overview](../../Alignment_Workbench_Unified_Application_API.md)


```python
class SpeakerHandler(Protocol):
    def list(self) -> tuple[Speaker, ...]: ...

    def get(self, speaker_id: UUID) -> Speaker: ...

    def create(self, speaker: Speaker) -> Speaker: ...
```

The initial speaker registry is append-only. Renaming or updating speaker metadata is excluded until speaker mutability is deliberately designed in both the database permissions and domain contract.
