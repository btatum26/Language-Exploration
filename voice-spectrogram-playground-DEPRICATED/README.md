# Voice Spectrogram Playground

Voice Spectrogram Playground is a local Streamlit application for learning what voices look like acoustically. Upload or record audio, play it, select a time region, and explore an interactive waveform, Fourier spectrum, linear-frequency spectrogram, log-Mel spectrogram, pitch, energy, and spectral centroid. Two recordings can also be viewed side by side with synchronized analysis settings.

Audio is decoded and analyzed inside the local Python process. The application has no accounts, database, cloud storage, or external audio service.

## Install

Python 3.11 or newer is required. Python 3.12 is a good default. The commands below install the application and test dependencies in a project-local virtual environment.

### Windows PowerShell

```powershell
cd voice-spectrogram-playground
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, run this once in that terminal and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux and macOS

```bash
cd voice-spectrogram-playground
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## Run

With the virtual environment active:

```text
streamlit run app.py
```

Streamlit prints a local URL, normally `http://localhost:8501`. Use the sidebar to upload files, record from the browser microphone, or add generated demonstration signals. Select a recording and processing settings, then drag the region slider to isolate a vowel, consonant, or transition.

Run the tests with:

```text
python -m pytest
```

## Supported audio

WAV, FLAC, and OGG are normally decoded by `soundfile`. MP3 works when the installed audio stack supports its codec. M4A and other compressed formats commonly require [FFmpeg](https://ffmpeg.org/) on `PATH`; when decoding fails, the interface reports this instead of discarding the error. Browser microphone recordings arrive as local audio bytes and are processed the same way as uploads.

## Visualizations

### Waveform

The waveform displays amplitude over time, a zero-amplitude line, the selected region, and an optional RMS-energy contour. Periodic portions often correspond to voicing; near-zero portions can be pauses or stop closures; abrupt transients can be release bursts. Absolute amplitude also reflects microphone gain and distance.

### Fourier spectrum

For a selection up to one second, the app computes a Hann-windowed FFT. For longer selections it averages short-time power spectra, which is a more honest summary of a changing utterance. Values can be viewed as magnitude, power, or relative decibels on linear or logarithmic frequency axes. Peaks might represent F0, harmonics, resonances, or noise depending on the selected sound.

### Linear-frequency spectrogram

This STFT view maps time to the horizontal axis, physical frequency in hertz to the vertical axis, and relative level in decibels to color. Voiced harmonics form narrow horizontal stripes. Broader concentrations of energy often reflect vocal-tract resonances. Fricatives produce diffuse noise and stops may show a closure followed by a broadband burst. F0, spectral centroid, and experimental LPC formant estimates can be overlaid.

### Mel spectrogram

The log-Mel view compresses the frequency axis, preserving more detail at low frequencies than at high frequencies. This representation is common as machine-learning input, although its bands are less physically direct than a linear-frequency plot.

### Acoustic tracks

`librosa.pyin` estimates the fundamental frequency in voiced frames. Unvoiced estimates remain missing rather than becoming a misleading zero-frequency trace. The app also plots RMS energy and spectral centroid. F0 depends on a periodic source; it is not expected during most silence, frication, or stop closures.

## Analysis controls

- **Window length** is how much audio each time-frequency frame sees. Shorter windows improve timing detail; longer windows sharpen frequency detail. The speech-oriented default is 25 ms.
- **Hop length** is the time between neighboring frames. A 10 ms hop creates overlapping frames and a smooth time view without changing the underlying recording.
- **FFT size** controls the frequency-bin grid. It must be at least as large as the window. Zero-padding to a larger FFT adds more plotted bins but does not create new acoustic resolution.
- **Dynamic range** sets how many decibels below each spectrogram's peak remain visible. A larger range exposes quiet detail and more background noise; the default is 80 dB.

Preprocessing always starts from the original decoded samples. Repeated setting changes therefore do not normalize, trim, or resample already processed audio. Stereo can be averaged to mono; when averaging is disabled, the first channel is used for scalar analysis.

## Export

The current waveform and linear spectrogram can be downloaded as PNG files. Select **Prepare PNG**, then use the download button that appears. The acoustic-track tab exports time, voiced status, F0, RMS energy, and spectral centroid as CSV. The sidebar exports the current processing and analysis controls as JSON. Filenames are derived from the active recording. PNG creation uses Kaleido and a locally installed Chrome/Chromium browser; it remains local. If no compatible browser is installed, `plotly_get_chrome` can install one for Kaleido.

## Comparison view

Add at least two uploads or demonstrations, then open **Compare**. The plots share the time range, visible frequency range, window, hop, FFT, dynamic range, and color scale. They are intentionally not aligned or subtracted. Differences can arise from anatomy, pitch, loudness, microphone, room, timing, and content—not only accent.

## Current limitations

- Audio-codec availability varies by operating system; M4A commonly needs FFmpeg.
- PNG export needs a local Chrome/Chromium installation for Kaleido.
- Browser microphone permission and input quality depend on the browser and device.
- Pitch tracking can octave-jump or fail in noise, creaky voice, very high or low pitch, and unvoiced sounds.
- Optional LPC formants are rough estimates. They are fragile for consonants, noisy audio, high-pitched voices, and rapidly changing speech.
- Each spectrogram is peak-relative, so identical colors across two plots indicate the same relative dB range, not calibrated sound pressure.
- Recordings must fit in local memory. The interface is intended for short educational examples rather than long corpora.

## Future work

The modules leave room for forced alignment between transcripts and audio, more robust formant analysis, dynamic time warping (DTW), aligned difference spectrograms, and controlled comparisons across accent groups. Those features require explicit research design and should not turn acoustic variation into automatic accent labels or judgments.

## Project layout

```text
app.py                         Streamlit interface and export controls
src/audio_io.py                Local decoding, metadata, and WAV encoding
src/preprocessing.py           Mono conversion, resampling, trimming, normalization
src/spectral_analysis.py       FFT/STFT/Mel, F0, energy, centroid, formants
src/plotting.py                Plotly figure builders
src/demo_signals.py            Deterministic synthetic demonstrations
src/explanations.py            Educational copy
src/constants.py               Central analysis defaults
tests/                         Focused signal-processing tests
```
