# Architecture boundary

```text
Qt Widgets
  │ intentions / render-only state
  ▼
EditorSession (integer-frame authority) ──► SessionAudioEngine adapter
  │                                           │
  │ background requests                       ▼
  ▼                                    Spectrogram Playground
RegistryServices facade                 decoder, mixer, playback,
  │ persistent SSH tunnel + engine      recorder, waveform/STFT/FFT
  ▼
RegistryWorkbenchService (Qt-free)
  ├── AlignmentService / MFA processing
  ├── repository protocols / PostgreSQL unit of work
  ├── immutable segment revisions
  └── corpus path + content-hash artifact resolution
```

## Qt UI

The main thread owns widgets only. `LibraryPanel`, `TimelineEditor`, `TrackWidget`,
`SoundInspector`, and `RecordingPanel` read central state and emit user intentions. Long-running
work is submitted through `TaskManager`, which tags results with request UUIDs and ignores stale
results after a replacement query.

## Editor state

`EditorSession` owns track order, source identities, clips, active/reference tracks, selection,
viewport, playhead, tool, mute/solo/gain, per-track display mode, and dirty edits. Frames at the
session sample rate are authoritative. Segment database timebases are preserved and converted only
at service/UI boundaries.

`SessionEventType` separates viewport, playhead, selection, segment, content, mix, and layout
changes. `TimelineEditor` coalesces pan deltas from every lane through one 16 ms single-shot timer,
then applies the accumulated integer-frame movement to the shared viewport. Viewport events update
plot ranges, tiers, and the scrollbar value; playhead and selection events touch only their own plot
items. Scrollbar range and step metrics are recalculated only when duration or zoom width changes.

## Audio and analysis

`SessionAudioEngine` renders non-destructive clip references to arrays outside the callback and
adapts them to Spectrogram Playground's `Project`, `Mixer`, and `AudioEngine`. One output stream and
one frame clock mix all tracks. The output callback remains free of decoding, I/O, logging, Qt, and
analysis. Waveform, spectrogram, spectrum, resampling, and microphone code is imported from the
existing native app rather than copied.

## Registry and PostgreSQL

`RegistryWorkbenchService` is a public, Qt-free application boundary inside `registry-aligner`.
The GUI never imports `storage.tables`, a SQLAlchemy engine, or MFA parsers. Catalog, detail,
speaker, version, revision, ingestion, and alignment calls pass through repository protocols and
unit-of-work transactions.

`RegistryServices` loads `alignment-workbench/.env`, starts the configured SSH tunnel in the
background connection task, and injects one pooled PostgreSQL engine into the backend for the
application lifetime. Shutdown waits for outstanding tasks before disposing the engine and stopping
the owned tunnel. Retry creates a fresh tunnel after a failed startup.

PostgreSQL stores metadata, versions, alignments, model segments, and immutable revisions. Audio
bytes remain in the configured corpus or remote object store. A resolved local cache is keyed by
the stored SHA-256; absolute workstation paths are never authoritative database state.
