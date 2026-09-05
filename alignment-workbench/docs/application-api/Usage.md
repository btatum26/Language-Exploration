# Application API Usage

**Status:** Implemented

[Application API overview](Alignment_Workbench_Unified_Application_API.md)

## Runnable application

The process composition root loads `.env`, validates configuration, starts and owns the SSH tunnel, owns the PostgreSQL pool and local storage, builds one `WorkbenchAPI`, performs harmless discovery reads during startup, and disposes the pool before stopping the tunnel on exit. No separately launched tunnel is required or accepted.

Required configuration names are:

- `REGISTRY_ALIGN_DATABASE_URL`: `postgresql+psycopg` URL for the runtime role. Store the credential in `.env`; startup errors never print it.
- `ALIGNMENT_WORKBENCH_AUDIO_ROOT`: writable directory for immutable managed WAV files.

`ALIGNMENT_WORKBENCH_RECOVERY_ROOT` is optional and defaults to `<audio-root>/recovery`.
The SSH alias defaults to `registry-db`, the executable defaults to `ssh`, and startup waits up to five seconds for the local host and port in `REGISTRY_ALIGN_DATABASE_URL`. These defaults can be overridden with `ALIGNMENT_WORKBENCH_SSH_ALIAS`, `ALIGNMENT_WORKBENCH_SSH_EXECUTABLE`, and `ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS`.

From `alignment-workbench`, launch the configured application with:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py
```

This starts the owned hidden SSH tunnel, validates PostgreSQL and local storage, and opens the
PySide6 reference client. The configured local database port must be free before startup. Closing
the window shuts down the database pool and terminates the tunnel. See the
[desktop reference client](../gui/Reference_Client.md) for the exact walkthrough.

## Manual import smoke path

Use a short uncompressed PCM WAV file and the same configured database/audio root:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/cli_main.py --import-audio 'C:\path\to\short.wav' --name 'Manual smoke' --language en
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
