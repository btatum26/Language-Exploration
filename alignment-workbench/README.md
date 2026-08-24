# Alignment Workbench

Alignment Workbench is a native PySide6 desktop editor for comparing recordings, inspecting
forced alignments, and saving immutable sound-boundary and label revisions. Its compact,
Audacity-inspired timeline keeps waveforms, spectrograms, word tiers, and phone/sound tiers on
one shared integer-frame clock.

## Architecture

The project is an independent GUI package with two local editable dependencies:

- `spectrogram-playground` supplies the proven decoder/resampler, shared-clock mixer and output
  engine, recording device integration, waveform envelopes, STFT spectrograms, spectrum analysis,
  analysis cache, and PyQtGraph timeline primitives.
- `registry-aligner` owns PostgreSQL access, migrations, recording/version ingestion, MFA
  processing, immutable revisions, remote audio transfer, and local artifact rules. The GUI calls
  `RegistryWorkbenchService`; it never opens a SQLAlchemy session or parses MFA output.
- `alignment_workbench.state` is the one authoritative editor state. It stores tracks, immutable
  source references, non-destructive clips, selection, viewport, playhead, display modes, and
  unsaved edits in integer frames.
- Qt widgets render that state and emit intentions. Database, decoding, hashing, analysis, and
  alignment work runs outside the GUI thread with request IDs and stale-result rejection.

See [docs/architecture.md](docs/architecture.md) for the detailed boundary map.

## Install

Python 3.11–3.13, FFmpeg/FFprobe, PostgreSQL access, and the existing registry-aligner MFA setup
are required. From this directory:

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv sync --extra dev
```

The local path dependencies keep `registry-aligner` and `spectrogram-playground` independently
launchable. No Qt package is added to `registry-aligner`.

## PostgreSQL and environment

Set `REGISTRY_ALIGN_DATABASE_URL` to a `postgresql+psycopg://` URL. The normal tunneled setup uses
`127.0.0.1:5433`. Keep the database URL and optional admin URL in this project's ignored `.env`:

```powershell
# alignment-workbench/.env
REGISTRY_ALIGN_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:5433/registry
REGISTRY_ALIGN_DATABASE_ADMIN_URL=postgresql+psycopg://ADMIN:PASSWORD@127.0.0.1:5433/registry

# PowerShell, optional configuration override
$env:ALIGNMENT_WORKBENCH_CONFIG='..\registry-aligner\registry-align.example.toml'
uv run registry-align db upgrade
```

By default the repository's `registry-align.example.toml` is loaded and its relative paths are
resolved against `registry-aligner/`, while database credentials are loaded first from
`alignment-workbench/.env`. The workbench opens one SSH tunnel in its connection worker, keeps the
tunnel and pooled database engine alive for the window lifetime, and closes both during shutdown.
`ALIGNMENT_WORKBENCH_CONFIG` overrides the TOML file. Credentials are never displayed; backend
connection errors redact URLs.

## Launch

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run alignment-workbench
```

The window still launches when PostgreSQL is unavailable. The status bar reports disconnection
and **Registry → Retry connection** retries tunnel startup in the background.

## Registry and local audio

The browser fetches metadata in pages of 50 and supports transcript, speaker, recording ID, word,
or sound search plus language and review filters. Double-clicking adds a track without replacing
existing tracks. Audio resolution first checks the configured logical corpus path and then the
content-hash cache. If necessary, Registry Aligner fetches the immutable remote object into
`.registry-align-cache/resolved-audio/`; machine-specific paths are not written to PostgreSQL.

## Alignment review

Select a word or sound tier interval to synchronize the playhead, selection, all plots, and the
inspector. Use the Boundary tool to drag interval edges. Focusing the label editor opens the
Italian IPA popup; symbols insert at the caret and replace selected text. **Accept + save**,
**Reject + save**, and **Needs review** create new `segment_revisions` rows. Original model segments
remain unchanged. Split and merge are also immutable revision operations; the backend validates
exact partitioning, adjacency, tier, and parent-word hierarchy. Reloading applies the latest accepted
revision.

## Audio editing

Source files are immutable. Cut, delete, split, paste, and move change `Clip` references containing
source bounds and a timeline start. **File → Import into active track** adds another source as an
undoable clip at the playhead; the regular Import action previews audio as a new track. Undo/redo
uses a Qt-free command stack. **File → Save as new recording** renders the active track to a new
WAV, fills the recording panel, and requires an
explicit recording ID, exact transcript, speaker, language, and **Save + align** action before
backend ingestion/versioning.

## Recording and import

Choose or create a speaker, then choose an input device, language, exact transcript, and recording
ID. **Start/Stop** makes
a local take and adds it for preview; **Cancel** removes the incomplete take without touching
PostgreSQL. **Import** follows the same preview path. **Save + align** uses backend hashing,
versioning, remote storage, and MFA alignment. Existing IDs produce explicit Cancel, Load existing,
or Create new version choices.

## Display and shortcuts

Each track independently supports **Both**, **Wave**, and **Spec**. Word and sound tiers remain
visible, and display changes preserve selection, playhead, zoom, and horizontal viewport. Drag the
thin handle at the bottom of a track to change its height; fixed-height tracks overflow into the
vertical scroller. The bottom timeline scrollbar and middle-drag panning move the shared waveform,
spectrogram, word, and sound viewport together.

| Key | Action |
| --- | --- |
| Space | Play/pause |
| Home | Jump to start |
| Left / Right | Move playhead |
| Shift+Left / Right | Extend selection |
| Escape | Close IPA popup or cancel edit |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z | Redo |
| Ctrl+X / C / V | Cut / copy / paste |
| Ctrl+Shift+O | Import audio into active track |
| Delete | Delete selection |
| + / - | Zoom in / out |
| S / M / B / R | Select / move / boundary / record |

Text-editing shortcuts are left to focused text fields.

## Tests and checks

Headless Qt tests set `QT_QPA_PLATFORM=offscreen` in `tests/conftest.py`.

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest
uv run ruff check .
uv run python -m compileall -q src
```

Registry backend checks remain independent:

```powershell
cd ..\registry-aligner
.\.pixi\envs\default\python.exe -m pytest -p no:cacheprovider
.\.pixi\envs\default\ruff.exe check .
```

PostgreSQL integration tests require `TEST_DATABASE_URL` pointing to a disposable PostgreSQL
database. SQLite is not used as a substitute.

## Troubleshooting

- **Disconnected:** verify the SSH tunnel, `REGISTRY_ALIGN_DATABASE_URL`, and run the schema upgrade.
- **Metadata but no audio:** correct the configured corpus root or verify Registry Aligner's remote
  SSH audio host; retry adding the recording.
- **MFA failure:** run `registry-align doctor`; the workbench uses the same models and wide-beam
  configuration as the CLI.
- **No microphone:** verify the selected PortAudio device and Windows input permissions.
- **Slow first display:** waveform and STFT caches are generated in worker threads; later rebuilds
  reuse cached results by track/generation identity.
