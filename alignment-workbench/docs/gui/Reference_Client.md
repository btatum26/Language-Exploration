# Desktop Reference Client

**Status:** Implemented first-pass PySide6 client

## Boundary and ownership

The desktop client is a thin caller of `WorkbenchAPI` and `RecordingEditSession`. It imports no
SQLAlchemy type, opens no database session, executes no query, and does not write analysis output
to PostgreSQL. `RecordingEditSession` is the only mutable recording authority. The GUI retains
only catalog DTOs and transient presentation state such as the active row, selected annotation,
waveform envelope, playhead, and background-task generation.

The client required no application-level DTO or query addition. Existing recording and library
list DTOs, public library version lookup, and edit-session properties expose the needed data.

## Structure

- `gui.app` starts the real `WorkbenchApplication`, injects its started API into the window, and
  shuts the window and application down in order.
- `gui.controller` coordinates catalog reads, import/open/save work, and session mutations without
  duplicating editable state.
- `gui.main_window` owns navigation, metadata, playback, annotation table and editor controls,
  dirty-state prompts, and visible loading/error/empty states.
- `gui.tasks` moves synchronous application calls to a Qt worker pool and delivers results on the
  GUI thread.
- `gui.waveform` computes a bounded PCM envelope off the GUI thread and paints the waveform,
  timeline, annotations, selection, and playhead.

PySide6 is the established desktop toolkit in the sibling Spectrogram Playground. This client
reuses its worker-result and generation-check patterns. Its existing waveform widgets are coupled
to the Spectrogram Playground `Project` and `Track` authorities, so this client uses a smaller
session-driven view instead of importing that second application model.

## Launch

Configure `.env`, leave the configured local database port free, and run from
`alignment-workbench`:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py
```

The composition root starts its owned hidden SSH tunnel, validates PostgreSQL and storage, and
then creates the window. Startup failures are shown without including the database URL. Closing
the window closes playback and workers before the application disposes PostgreSQL and stops SSH.

The prior non-GUI smoke command remains available as:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/cli_main.py
```

## Manual walkthrough

1. Launch the command above. Confirm both navigation panels leave `Loading` and show either their
   database-backed rows or explicit empty states.
2. Select a short uncompressed PCM WAV file with `Import Recording`, supply its name and language,
   and wait for it to appear selected in the recordings list and open in the workspace.
3. Confirm the workspace shows its UUID, speaker or `Not assigned`, language, duration, audio
   format, revision UUID, clean/synchronized state, and prepared waveform.
4. Select an annotation library and use `Pin latest`. If no library has a published version, the
   editor correctly has no concept choice and annotation creation stays disabled.
5. Select a concept and compatible geometry, enter sample and optional frequency bounds, and use
   `Create`. Confirm the row and painted lane appear immediately.
6. Select the row, inspect its deterministic JSON, change bounds, concept, or note, and use
   `Update selected`. Use `Delete` to exercise removal if wanted.
7. Use Undo and Redo and confirm the table, painted annotation, details, dirty flag, and action
   enablement track the session immediately.
8. Enter an optional author/message and use `Save Revision`. Confirm the revision number/UUID and
   clean state update. A queued or conflicted save is reported distinctly.
9. Close the recording, reopen it from the list, and confirm the saved annotation is loaded from
   the public open path.
10. Use Play/Pause and Stop to verify local audio playback and the synchronized playhead.
11. Close the window. Dirty work requires an explicit discard confirmation; active background
    work must finish before exit. The application then performs clean database and SSH shutdown.

## Current gaps

- `RecordingEditSession` exposes no stable split or merge operation, so the client does not invent
  one.
- Library browsing exposes catalog summary and latest-version pinning, not full version history or
  search.
- The first client has a waveform and annotation lane but no spectrogram or direct paint gesture.
- Recovery decisions remain outside this milestone; pending/conflicted state is visible through
  session status and errors.
- Only uncompressed PCM WAV import is supported by the application audio-storage contract.
