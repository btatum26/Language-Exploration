# Desktop workstation

The desktop entry point now opens `WorkstationWindow`: a fixed transport toolbar,
a vertically scrollable multitrack timeline, and one resizable annotation inspector.
Recordings, libraries, and the annotation table are not permanent main-window panels.

## Launch

From `alignment-workbench`, configure `.env` with `REGISTRY_ALIGN_DATABASE_URL`
(`postgresql+psycopg`), `ALIGNMENT_WORKBENCH_AUDIO_ROOT`, and, if needed,
`ALIGNMENT_WORKBENCH_RECOVERY_ROOT` and `ALIGNMENT_WORKBENCH_SSH_ALIAS`.
The application owns its SSH tunnel, PostgreSQL pool, and shutdown. A small
standard-library guardian owns SSH and watches a pipe from the application;
closing the application normally or forcibly closes the pipe and stops SSH.
Other applications' active tunnels are never reused or terminated automatically. Leave its
configured local tunnel endpoint free. Apply the existing migrations before launch.

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py
```

Startup retains bundled Core, Phonetics and Prosody publication initialization.
See [Bundled libraries](../application-api/Bundled_Libraries.md). No new migration
or recording/domain API redesign is required by the workstation.

## Recording workflow

**+ Add recording** offers **Record new audio**, **Import audio file**, and
**Browse existing recordings**. Microphone capture offers device selection, a level
meter, start/stop, and adding the recorded take through the existing import API.
Temporary takes are cleaned up after import or cancellation. Import retains
WAV/MP3 inspection, immutable storage, the core
publication pin and initial revision save. The recordings dialog offers search,
duration, revision, audio availability, refresh, add and cancel. Discovery uses
the existing bounded catalog (up to 500 recordings).

Each recording opens once, initially at workspace time zero. Its track header
contains its name, mute, solo, waveform/spectrogram selector and overflow menu.
The menu provides details, track reordering, marker creation, definition refresh,
and removal. Removing a track closes only its edit session; it never deletes the
stored recording. Dirty removal prompts to save; cancel keeps the track open.

Drag the clip header to change placement. Drag the audio to select an interval.
Offsets clamp to zero. Track offsets, order, mute/solo and view state are temporary
and reset on restart. They are never written into recording revisions. An
annotation at local 0.5 seconds on a clip offset by 2 seconds displays at 2.5 seconds.

## Shared timeline and inspector

All tracks use one workspace viewport, selection and device-clock playhead.
The mouse wheel scrolls vertically through tracks. Ctrl+wheel zoom and
middle-drag/Shift-wheel pan operate on the shared horizontal viewport.
The toolbar provides fit, zoom to selection, zoom in/out and clear selection;
the horizontal scrollbar pans all tracks. Tracks start at 280 logical pixels high;
drag the bottom edge of a track to resize it independently. Track heights are
preserved when the window resizes or tracks are reordered, with sufficient minimum
height for annotation lanes. Overflow scrolls vertically. The toolbar stays fixed.

Click a track to activate it. Its header is highlighted. Dragging audio opens the
creation form; clicking an annotation opens the edit form. Annotations from different libraries always occupy separate lanes. Within a
library, overlapping content splits by category (for example vowels/consonants),
or definition when no category exists (for example words/syllables). Further
overlap splits by individual definition, then by occurrence only when necessary.
Rows are ordered deterministically and do not change when zooming or moving clips.
Rendering, selection and boundary handles all use the same lane assignment. Seconds fields
convert to exact recording-local samples through the original editor controls.
Label, note, concept, geometry and frequency controls retain their existing validation.
**Revert form** restores the selected annotation; **Cancel / clear selection** returns
to the empty inspector. Markers remain accessible from the track menu.

The concept picker searches names, symbols and aliases and filters categories.
Published library definitions are fetched on workers without changing recordings.
Creation or an explicit concept update pins the selected publication through the
existing edit session. Existing namespace pins retain their exact version. Library
pinning and annotation changes retain the session's separate undo operations.
Library/version information is secondary in the selected concept, with no permanent
library browser. Refresh definitions is available from the track menu.

The existing bounded waveform and spectral renderers are reused, including detail
workers, stale-result checks, MP3 decoding and the 8 kHz/Nyquist frequency cap.
No placeholder signal rendering is used. Existing polygons can be inspected and
preserved but are not editable. Attributes/confidence and polygon vertices remain
available in annotation JSON; dedicated authoring controls are outside this draft.

## Playback

One `WorkspacePlayer` mixes into one `QAudioSink` using its processed-audio clock.
Mute excludes a track; when any track is soloed, only soloed and unmuted tracks
are audible. Tracks are silent outside their clip placement. Seek, pause and stop
operate on the workspace. Changing placement or mute/solo flushes queued audio
and resumes at the current shared position.

The first mixer emits stereo float PCM at 48 kHz, duplicates mono channels, uses
linear rate conversion, and clips summed samples to [-1, 1]. It uses the first
two channels of multichannel sources. It decodes bounded blocks with MP3 preroll,
closing decoder handles per block. An incompatible/missing output device or decoder
failure produces a visible error. Higher-quality resampling, multichannel downmix,
large-session streaming optimization and device selection are later work. There is
no separate per-track media clock. Automated output checks verify device progression;
speaker/headphone listening quality still requires a human listening check.

## Ownership and saves

- `workspace.py`: temporary track placement/order/mix state, active track, shared
  viewport, selection conversion and playhead.
- `workstation_window.py`: main-window composition, recording dialog and coordinated
  add/remove/save/recovery/shutdown actions.
- `track_widget.py`: track header, clip drag handle, timeline wiring and adapter to
  the original recording editor.
- `annotation_inspector.py`: seconds fields and form reversion around the existing
  annotation form and authoritative edit session.
- `workspace_player.py`: bounded mixer and the single output clock.
- `waveform.py` / `spectrogram.py`: existing signal renderers and annotation gestures.

The original `main_window.py` remains a tested recording-editor implementation.
The track adapter reuses its form, action validation and preview workers; its reference
shell is hidden, and its form and waveform are reparented into the workstation.
The adapter does not populate or use its annotation table as selection state and
does not load a per-recording playback source. Extracting the remaining legacy shell
construction is a later cleanup; recording state is not duplicated.

Undo, redo and Save revision affect the active recording. Save prompts for optional
author/message. Closing the workspace waits for outstanding work, then saves every
dirty track before closing any sessions. Cancel, queued/failed saves and conflicts
keep the workspace open. Pending saves retain their durable recovery files and must
synchronize before close. **Retry pending saves** is available in the toolbar and
error banner. Recovery runs sequentially across sessions and blocks editing while
clean sessions reload. It does not overwrite conflicts or discard pending data.
Shutdown stops playback and preview workers and closes the existing session/audio
leases before the application shuts down its owned PostgreSQL/SSH runtime.

## Verification

`tests/test_workspace.py` exercises real application edit sessions over the contract
persistence store with real WAV audio: two simultaneous tracks, waveform/spectrogram,
shared navigation and playhead, clip dragging, offset/local conversion, overlapping
lanes, marker creation, inspector create/update/delete/revert paths, all-library
selection, undo, save-on-close, removal and reopening. Both immediate and real Qt
worker runners are exercised. A numeric mixer test verifies audio samples for offsets,
sample rates, stereo/mono, silence, mute and solo. A device-dependent test exercises
play/pause/seek/stop. Screenshots are written to `.artifacts/workstation/`.

```powershell
uv run pytest tests/test_workspace.py
uv run pytest
uv run mypy --python-version 3.13 src
uv run ruff check src tests
```

Existing recording-editor, save/recovery, waveform/spectrogram, application and domain
tests remain regression checks. PostgreSQL integration tests require explicitly
configured disposable targets (`TEST_DATABASE_URL`, `TEST_RUNTIME_DATABASE_URL`);
never run destructive fixtures against the production schema.

## Verification in this checkout

The September 11 workstation pass completed 157 tests, with 41 PostgreSQL tests
skipped because disposable database targets were not configured. Ruff and strict
mypy passed. The Windows output device passed play/pause/seek/stop checks. Two
repository MP3 recordings (`088.mp3` and `tramontana.mp3`) were also opened through
real application import/edit sessions backed by the contract store, rendered as
waveform and spectrogram, offset and zoomed together, and captured in
`.artifacts/workstation/representative-mp3.png`. The synthetic annotation workflow
is captured in `.artifacts/workstation/two-tracks.png`.

Configured PostgreSQL application startup was attempted but refused to start
because its owned tunnel endpoint `127.0.0.1:5433` was already occupied. The
existing listener was left untouched. These GUI checks do not establish live
PostgreSQL save/reopen behavior.

Follow-up coverage includes semantic library/category/definition lane allocation,
independent track resizing and overflow scrolling, microphone format conversion and
import/reopen, and real-process tunnel cleanup after normal exit and parent crashes.
The occupied-port issue was traced to orphaned SSH processes; the guardian now
prevents those leftovers after application termination.
