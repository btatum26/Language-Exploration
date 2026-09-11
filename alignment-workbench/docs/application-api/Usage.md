# Application API Usage

**Status:** Implemented

[Application API overview](Alignment_Workbench_Unified_Application_API.md)

## Runnable application

The process composition root loads `.env`, validates configuration, starts and owns the SSH tunnel, owns the PostgreSQL pool and local storage, builds one `WorkbenchAPI`, checks storage/schema availability and initializes the three bundled libraries during startup, and disposes the pool before stopping the tunnel on exit. No separately launched tunnel is required or accepted. A guardian process owns SSH and watches an application-owned pipe; EOF stops SSH even when the application crashes or is forcibly closed. Existing listeners are not automatically terminated or reused.

Required configuration names are:

- `REGISTRY_ALIGN_DATABASE_URL`: `postgresql+psycopg` URL for the runtime role. Store the credential in `.env`; startup errors never print it.
- `ALIGNMENT_WORKBENCH_AUDIO_ROOT`: writable directory for immutable managed WAV and MP3 files.

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

Use a short uncompressed PCM WAV or MP3 file and the same configured database/audio root:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/cli_main.py --import-audio 'C:\path\to\short.mp3' --name 'Manual smoke' --language en
```

The command lists the existing catalog, copies and fingerprints the source without changing it, creates revision one, checks that discovery returns the imported recording, opens and closes it, and finally shuts down the application. Expected terminal markers are `application=started`, `import_open_close=ok`, and `application=shutdown`.

## Bundled publications

See [Bundled libraries](Bundled_Libraries.md) for the core, phonetics, and prosody inventories,
file format, GUI steps, and fresh-database transition from the old test-only placeholder.

## Exact core publication

`workbench.ensure_core_library()` returns the persisted `LibraryVersion` for `core@0.1`.
`WorkbenchApplication.start()` invokes `ensure_bundled_libraries()` after availability checks, before initial GUI discovery.
For directly composed APIs, call it explicitly when initialization is wanted. It uses normal
`LibraryHandler` operations and the canonical content hash, reuses existing matching content,
recovers a missing publication, and verifies concurrent winners. Conflicting immutable content
fails clearly; it never substitutes `latest`, renames publications, or runs migrations.
Identical content under another version label also fails clearly because storage enforces one
content hash per library.

New GUI imports put this exact manifest entry in the initial creation command:

```python
from models import PinnedLibraryVersion

core = workbench.ensure_core_library()
core_pin = PinnedLibraryVersion(namespace="core", version="0.1", content_sha256=core.content_sha256)
# Pass libraries=(core_pin,) to CreateRecordingCommand.
```

For an existing recording, explicitly call `session.pin_library_version(core)` and save normally.
Opening never adds a pin implicitly. `ConceptRef("core@0.1:silence")` accepts a `TimeIntervalGeometry`
and requires no attributes. Initialization and persistence operations are synchronous application
calls; a GUI must use its worker boundary for them.

## Editing and saving

```python
workbench = create_workbench(
    persistence=persistence,
    audio_storage=audio_storage,
    recovery_outbox=recovery_outbox,
)

session = workbench.recordings.open(recording_id)

concept = next(entry for entry in session.list_available_concepts() if entry.entry_key == "vowel-a")

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
