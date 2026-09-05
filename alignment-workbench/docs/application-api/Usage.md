# Application API Usage

**Status:** Implemented

[Application API overview](Alignment_Workbench_Unified_Application_API.md)

## Runnable application

The process composition root loads `.env`, validates configuration, owns the PostgreSQL pool and local storage, builds one `WorkbenchAPI`, performs harmless discovery reads during startup, and disposes the pool on exit.

Required configuration names are:

- `REGISTRY_ALIGN_DATABASE_URL`: `postgresql+psycopg` URL for the runtime role. Store the credential in `.env`; startup errors never print it.
- `ALIGNMENT_WORKBENCH_AUDIO_ROOT`: writable directory for immutable managed WAV files.

`ALIGNMENT_WORKBENCH_RECOVERY_ROOT` is optional and defaults to `<audio-root>/recovery`.

From `alignment-workbench`, launch the configured application with:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py
```

This starts the real application, validates PostgreSQL and local storage, lists recordings and annotation libraries, and shuts down. It does not mutate PostgreSQL.

## Manual import smoke path

Use a short uncompressed PCM WAV file and the same configured database/audio root:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py --import-audio 'C:\path\to\short.wav' --name 'Manual smoke' --language en
```

The command lists the existing catalog, copies and fingerprints the source without changing it, creates revision one, checks that discovery returns the imported recording, opens and closes it, and finally shuts down the application. Expected terminal markers are `application=started`, `import_open_close=ok`, and `application=shutdown`.

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
session.close()
```

## External analysis

External analysis uses the same editing surface and returns ordinary annotations:

```python
session = workbench.recordings.open(recording_id)
proposed_annotations = cli_analysis(session.audio.local_path)
session.add_annotations(proposed_annotations)
session.save(message="Apply CLI analysis")
```
