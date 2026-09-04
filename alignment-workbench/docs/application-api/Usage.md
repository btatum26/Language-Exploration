# Application API Usage

**Status:** Proposed; the `WorkbenchAPI` and edit-session implementation do not yet exist

[Application API overview](Alignment_Workbench_Unified_Application_API.md)

## Editing and saving

```python
workbench = create_workbench(
    persistence=persistence,
    audio_storage=audio_storage,
    recovery_outbox=recovery_outbox,
)

session = workbench.recordings.open(recording_id)

concept = next(
    entry
    for entry in session.list_available_concepts()
    if entry.entry_key == "vowel-a"
)

annotation = session.create_annotation(
    concept_ref=ConceptRef("italian@1.0:vowel-a"),
    geometry=TimeIntervalGeometry(
        start_sample=24_000,
        end_sample=31_000,
    ),
    note="Open vowel",
)

session.replace_annotation(
    annotation.id,
    annotation.model_copy(update={"note": "Confirmed open vowel"}),
)

result = session.save(
    author="Ben",
    message="Verify first vowel",
)
```

## External analysis

External analysis uses the same editing surface and returns ordinary annotations:

```python
session = workbench.recordings.open(recording_id)
proposed_annotations = cli_analysis(session.audio.local_path)
session.add_annotations(proposed_annotations)
session.save(message="Apply CLI analysis")
```
