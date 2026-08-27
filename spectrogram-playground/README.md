# Spectrogram Playground

Spectrogram Playground is a native, local desktop workspace for comparing voice recordings on one synchronized timeline. It provides waveform, linear/Mel spectrogram, pitch/energy, selection-driven Fourier views, and basic selection editing. It is an educational analysis tool, not an accent classifier or full audio-production editor.

## Current scope and limitations

- Every imported clip begins at time zero. Start/end trimming is non-destructive. Delete, cut, paste, and move modify the active track's prepared playback/analysis buffers for the current session; the decoded source samples remain retained. There is no effects chain.
- Analysis is mono. Original decoded samples and channel metadata are retained.
- WAV, FLAC, and OGG use libsndfile directly. MP3 works only when the local libsndfile build supports it; otherwise convert with FFmpeg.
- Spectrograms are cached in memory and intended for recordings measured in seconds or minutes.
- Playback and microphone capture require a working PortAudio device. Automated tests use fake backends.

## Architecture

`spectrogram_playground/model` owns project, track, transport, selection, viewport, and settings state. `spectrogram_playground/audio` decodes and resamples immutable source audio, mixes prepared 48 kHz buffers in one callback-driven output stream, and records microphone input. `spectrogram_playground/analysis` computes and caches waveform envelopes, STFT/Mel data, F0/RMS, and spectra. Bounded worker pools run every expensive analysis operation; only completed results cross Qt signals back to the UI thread. `spectrogram_playground/ui` renders those results with Qt Widgets and PyQtGraph. A Qt timer only redraws the authoritative audio-frame position.

Playback buffers default to 48 kHz for device output. Separate analysis buffers default to 16 kHz for speech-oriented transforms. Import never overwrites the decoded source samples.

## Setup and run

Windows PowerShell:

```powershell
cd spectrogram-playground
$env:UV_CACHE_DIR='.uv-cache'
uv sync --extra dev
uv run python app.py
```

Linux or macOS:

```bash
cd spectrogram-playground
UV_CACHE_DIR=.uv-cache uv sync --extra dev
uv run python app.py
```

Linux may require the system PortAudio package (for example `libportaudio2`). Tests and lint:

```powershell
uv run pytest
uv run ruff check .
```

## Controls

- `Space`: play/pause; `Home`: beginning; `Left`/`Right`: seek 50 ms
- `Shift+Left`/`Shift+Right`: seek 500 ms; `+`/`-`: zoom
- Left-click or left-drag a timeline, waveform, or spectrogram to move the playhead
- `Ctrl`+left-drag defines or changes that track's selection and makes the track active; `Escape` clears the active track's selection
- The mouse wheel scrolls vertically through tracks; `Shift`+wheel pans horizontally along the shared timeline. Middle-drag also pans horizontally. Dragging is measured in screen pixels so the viewport remains stable while it moves.
- `Ctrl+C` copies the active track's selected audio; `Ctrl+X` cuts it; `Ctrl+V` pastes at the playhead; `Delete` removes it
- `Ctrl+M` moves the active track's selection to the playhead. Each track retains its own selection; copy/paste uses an in-app audio clipboard and can cross tracks.
- Track headers provide rename, active selection, mute, solo, visibility, gain, reorder, and remove controls
- Tracks have a 145 px minimum height. Drag the thin bottom edge of a track to resize it; the track list scrolls vertically when its lanes exceed the available space.
- The Track menu can trim the active track's start or end to the playhead, keep only that track's selection, or reset the trim. To align recordings, make each track active in turn, seek to its spoken onset, and choose **Trim active track start to playhead**.
- The first Record click asks for an input device. While recording, a red strip above the timeline shows the selected device, elapsed time, live input level, and a Stop recording button.

## Visualizations

Waveform displays time-domain amplitude using cached min/max envelopes. Spectrogram displays frequency energy through time with shared speech-oriented STFT settings; Mel mode groups frequencies perceptually. Optional F0 and RMS overlays show estimated vocal-fold repetition and energy. Experimental F1-F3 overlays use LPC spectral-envelope roots to estimate vocal-tract resonances and are most reliable on steady voiced vowels. Fourier Transform compares the active track's selection, or a labeled short window around the playhead when there is no selection. Short regions use a windowed FFT and long regions use deterministic Welch averaging.

## Export

The File menu exports the active visualization as PNG, cached F0/F1/F2/F3/RMS tracks as CSV, and project/analysis settings as JSON. JSON export contains paths and metadata, never raw audio.

## Audio troubleshooting

If a file cannot be decoded, validate it or convert it to WAV/FLAC/OGG with FFmpeg. If playback or recording fails, use **Audio → Select input device** and avoid virtual streaming inputs when you intend to use a physical microphone. The recorder uses the selected device's native default rate, then prepares a separate project-rate playback copy. Close applications holding the device exclusively if opening it still fails. Run `uv run python -c "import sounddevice as sd; print(sd.query_devices())"` to list devices.

## Windows packaging

```powershell
uv run pyinstaller SpectrogramPlayground.spec --clean --noconfirm
```

The initial executable appears under `dist/SpectrogramPlayground/`. Packaging does not bundle external FFmpeg binaries.

## Future work

Planned research extensions include annotations, forced alignment, dynamic time warping (DTW), aligned difference spectrograms, formant trajectories, and speaker-normalized comparisons. They are deliberately outside this release.
