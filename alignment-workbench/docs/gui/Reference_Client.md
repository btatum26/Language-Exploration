# Desktop Reference Client

**Status:** Implemented first-use manual annotation workflow

## Boundary and ownership

The desktop client calls `WorkbenchAPI` and `RecordingEditSession`. GUI classes contain no
SQLAlchemy access or database queries. The edit session owns recording validation, mutations,
undo/redo, library pins and revision saves. Selection, viewport, playhead, selected annotation ID
and boundary-drag previews belong to the GUI and are never stored in recording snapshots.

Catalog reads, import/open/save operations, library retrieval and audio decoding use workers.
Annotation gestures commit through the edit session only on release. Audio analysis remains a
workbench responsibility outside the data-handler layer. Existing pending/conflict recovery
rules and the application's owned SSH/database lifecycle remain in place.

## Launch and bundled initialization

Configure `.env`, run `uv run alembic upgrade head`, leave the configured local database port free,
and run from `alignment-workbench`:

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run python src/main.py
```

Startup opens its hidden SSH tunnel, checks PostgreSQL/schema and local storage availability,
then calls `WorkbenchAPI.ensure_bundled_libraries()` before creating the window and refreshing its
catalog. This operation uses the existing library creation/publication handler and
`library_content_sha256()`; it is content initialization, not a schema migration.

The persisted publications are **Core annotations** (7 entries), **Phonetics** (29 starter IPA
sounds), and **Prosody** (3 qualitative pitch contours), all version **0.1**. See
[Bundled libraries](../application-api/Bundled_Libraries.md) for inventories, format, source,
and the concrete fresh-database transition for an existing test-only core publication.

Repeated startups reuse the same publication, including its real content hash. A core identity
without version 0.1 receives that exact publication. Concurrent initializers re-read and verify
the winner after a rejected competing write. Conflicting content under `core@0.1` stops startup
with a clear error and leaves the existing publication intact. No later version is substituted.
The storage contract also requires unique content hashes within a library: if identical required
content already exists under a different version label, startup reports that conflict. It does
not rename that publication or change the schema to manufacture version 0.1.

The CLI smoke entry point remains `uv run python src/cli_main.py`. Core initialization applies to
application startup; automatic inclusion of the core pin in recording creation is a GUI import
policy. Other callers still explicitly supply `CreateRecordingCommand.libraries`.

## First recording

1. Use **Import Recording**, choose a short PCM WAV or MP3, and supply a name and language.
2. Wait for the waveform. Revision one is already saved with the exact `core@0.1` pin and its
   canonical content hash. **Silence** and **Time Interval** are selected automatically.
3. Drag across the waveform below the annotation lane to select an interval. Either direction
   works; coordinates clamp to the recording. Shading and readouts show start, end and duration
   in seconds, with exact sample values retained.
4. Enter an optional **Label**, adjust sample boundaries, or enter a note in **New annotation** mode. Use
   **Create annotation**. Interval creation requires a waveform selection. For a marker, choose **marker** and click
   a waveform position or enter **Start sample**; no interval is required.
5. The new annotation is selected in the table, lane and editor. Change the label, sample bounds or note and
   choose **Apply edits**, or hover over an interval label's left/right edge in the annotation lane.
   A horizontal resize cursor and highlighted edge show where to drag its start/end time, with a
   10-pixel grab area on either side. No prior selection is needed. Overlapping labels use the
   nearest edge, preferring the selected label when edges coincide. A boundary
   drag previews locally and creates one undo step on release. Escape cancels the preview;
   invalid boundaries restore the authoritative geometry and show a concise error.
6. Use **Undo**, **Redo**, or **Delete selected**. The table, editor and overlay follow the session.
   Click an annotation or its table row to select it again. Use **New annotation** or make another
   waveform selection to return to creation mode.
7. Choose **Save Revision** (`Ctrl+S`). The compact save dialog accepts an optional author and
   revision message. A successful save shows a new revision and clean state. Pending and conflict
   results remain distinct from a successful database save.
8. Close the window, launch again, select the recording and choose **Open Recording**. The saved
   label, geometry, note and exact library pin are restored.

**Label** is the annotation's own display text. It appears in the table's first column and on the
annotation bar, independently of its concept and note. Set it before creation or edit it with
**Apply edits**; label changes use the same undo/redo and revision-save path as other edits.
Blank labels display the exact pinned definition's IPA symbol or display name, with a full
concept-reference fallback. Older snapshots without a label remain valid; fallback text is
never stored as a label. Existing `attributes["label"]` values are not used as labels.
The `20260906_0003_annotation_label` migration adds a nullable `annotations.label` column without
backfilling or rewriting historical annotations. Upgrade the database before launching this version.

For an older recording without `core@0.1`, use the visible **Use core library (core@0.1)** button
at the top of the editor. This pins through the edit session, marks it dirty, and requires
**Save Revision** to persist. Opening never silently changes an older snapshot. Undoing the pin,
or removing it through the public edit session, leaves it absent until explicitly added again.
The editor distinguishes loading, retrieval failure, no published libraries, and no pinned
libraries. **Refresh Libraries** retries a failed catalog retrieval. General library browsing
supports **Pin latest** for Phonetics and Prosody as explicit actions. The concept editor
searches registered names, symbols, and aliases, with a shallow category filter.

## Navigation and layout

- **Audio view** above the plot switches between **Waveform** (default) and **Spectrogram**.
  Both retain the same viewport, selection, annotations and playback position. Click, drag,
  zoom and pan work in either view. The choice affects only the display, not saved revisions.
  Spectrogram frequency runs from 0 Hz at the bottom to 8,000 Hz at the top (or half the sample
  rate when that is lower);
  brighter colors show stronger energy on a fixed -90 to 0 dB amplitude scale.
  WAV and MP3 previews load on a worker after navigation settles and are reused when toggling
  back at the same viewport. Analysis uses 25 ms Hann windows (capped at 4,096 samples),
  averages channel powers, and samples at most 1,200 time columns. Long overviews use spaced
  windows and can miss brief events; zoom in for finer time detail. Stale results are discarded.
- Click the waveform to move the playhead and seek playback. A drag selects an interval and
  does not repeatedly seek. A seek requested while media loads is applied when it becomes seekable.
- Use the mouse wheel or **Zoom + / Zoom -** for horizontal zoom. Wheel zoom anchors at the cursor.
- Middle-button drag or Shift+wheel pans horizontally. **Zoom to selection** fits the selected
  interval; **Fit recording** restores the whole recording; **Clear selection** removes shading.
- Waveform, annotation lane, timeline, selection and playhead share one sample-based viewport.
  A bounded whole-recording envelope appears first. After zoom/pan settles, a worker decodes a
  bounded envelope for that viewport, reaching individual samples at close zoom. Stale results
  are discarded. Selection/boundary mouse moves do not decode audio.
- Drag the horizontal divider to resize waveform versus lower panels. Vertical dividers resize
  navigation, table and editor. The editor scrolls and keeps controls at their useful minimum
  sizes; form labels wrap to a separate row when needed.
- **Recording details** expands technical metadata. **Annotation JSON details** expands the
  deterministic annotation JSON. The table emphasizes seconds and notes; sample values and full
  references are available in tooltips and the inspector. Frequency fields are hidden for
  time-only geometry.

Geometry type and concept are locked while editing an existing annotation. Create another
annotation for a different type. Polygon creation and editing are not offered; existing polygons
can be inspected and are preserved by saves. Time intervals support boundary dragging; existing
points and time-frequency boxes support the available numeric controls. Attribute authoring,
polygon vertices, full library authoring, split/merge and automatic analysis remain
outside this pass. The sample spinboxes currently cover up to 2,147,483,647 samples.

## Playback and shutdown

The existing `QMediaPlayer` is retained. Play/Pause depends on media readiness and an available
output device. Loading, invalid media, player errors, seekability and playback state appear beside
the transport controls. Playback diagnostics survive unrelated annotation/save notices. **Stop**
resets position to zero. Annotation edits and catalog refreshes do not reset an unchanged source.

Mutation controls and gestures are disabled during blocking application work, including saving.
Dirty recordings require an explicit discard before close. The window stops playback and waits
for its workers; the application then closes PostgreSQL and stops its owned SSH tunnel.

## Regression verification

`tests/test_core_library.py` exercises real application initialization with a contract store.
`tests/test_first_use_gui.py` exercises real edit sessions with mouse selection, boundary drags,
creation, numeric/note edits, cancellation, invalid geometry, undo/redo/delete, explicit pinning,
polygon preservation, busy-state protection, playback diagnostics and save/reopen.

GUI tests show and render 1024x768 and 1340x850 logical-pixel windows with a normal Windows font.
They scroll every important editor control into the viewport and use mouse clicks for actions.
Screenshots are written to `.artifacts/first-use/`. The layout workflow is also checked with
`QT_SCALE_FACTOR=1.5`. This verifies accessibility and rendering, not only programmatic button calls.

`tests/database/test_core_first_use_postgresql.py` creates a fresh migrated disposable schema per
test. It uses real commits, separate API/persistence instances and fresh windows for save/reopen,
and synchronized independent database sessions for racing initialization. It also covers a core
identity without 0.1, a later distinct publication, conflicting 0.1 content, and identical content
under another immutable label. Supply `TEST_DATABASE_URL` with permission to create disposable
schemas; these tests never migrate or remove the production schema.

```powershell
uv run pytest tests/test_core_library.py tests/test_first_use_gui.py
uv run pytest tests/database/test_core_first_use_postgresql.py
```

Windows verification passed Play, Pause, seek, synchronized playhead and Stop using a silent WAV
and an available output device. Audible output on the user's chosen speakers/headphones remains
a manual listening check. Tests skip the device-dependent smoke check when no output is available.
