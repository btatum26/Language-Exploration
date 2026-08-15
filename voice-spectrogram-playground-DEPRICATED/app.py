"""Streamlit interface for Voice Spectrogram Playground."""

from __future__ import annotations

import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import re

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from src.audio_io import AudioLoadError, AudioRecording, audio_to_wav_bytes, load_audio_bytes
from src.constants import COLORMAPS, DEFAULTS, SUPPORTED_EXTENSIONS
from src.demo_signals import generate_demo
from src.explanations import COMPARISON_WARNING, FORMANTS, LINEAR_SPECTROGRAM, MEL_SPECTROGRAM, SPECTRUM, WAVEFORM
from src.plotting import spectrogram_figure, spectrum_figure, track_figure, waveform_figure
from src.preprocessing import ProcessingResult, preprocess_audio, select_region
from src.spectral_analysis import (
    AcousticTracks,
    SpectrogramResult,
    calculate_spectrum,
    estimate_acoustic_tracks,
    estimate_formants,
    linear_spectrogram,
    mel_spectrogram,
)


st.set_page_config(page_title="Voice Spectrogram Playground", page_icon="🎙️", layout="wide")


@st.cache_data(show_spinner=False)
def decode_audio(data: bytes, filename: str) -> AudioRecording:
    """Cache decoding while preserving source samples across setting changes."""

    return load_audio_bytes(data, filename)


@st.cache_data(show_spinner=False)
def process_recording(
    samples: np.ndarray,
    sample_rate: int,
    mono: bool,
    target_rate: int | None,
    trim: bool,
    normalization: str,
) -> ProcessingResult:
    return preprocess_audio(
        samples,
        sample_rate,
        mono=mono,
        target_rate=target_rate,
        trim=trim,
        normalization=normalization,
        silence_top_db=DEFAULTS.silence_top_db,
    )


@st.cache_data(show_spinner=False)
def cached_linear(
    samples: np.ndarray, sample_rate: int, window_ms: float, hop_ms: float, n_fft: int, dynamic_range: float
) -> SpectrogramResult:
    return linear_spectrogram(
        samples,
        sample_rate,
        window_ms=window_ms,
        hop_ms=hop_ms,
        n_fft=n_fft,
        dynamic_range_db=dynamic_range,
    )


@st.cache_data(show_spinner=False)
def cached_tracks(
    samples: np.ndarray, sample_rate: int, window_ms: float, hop_ms: float
) -> AcousticTracks:
    return estimate_acoustic_tracks(
        samples,
        sample_rate,
        window_ms=window_ms,
        hop_ms=hop_ms,
        fmin=DEFAULTS.pitch_min_hz,
        fmax=DEFAULTS.pitch_max_hz,
    )


def safe_filename(source: str, kind: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(source).stem).strip("-.") or "recording"
    return f"{stem}-{kind}.{extension}"


def png_download(figure: go.Figure, source: str, kind: str) -> None:
    """Render a Plotly figure on request and expose it as a download."""

    state_key = f"png_export_{kind}"
    signature = hashlib.sha256(figure.to_json().encode("utf-8")).hexdigest()
    if st.button(f"Prepare {kind} PNG", key=f"prepare_{kind}"):
        try:
            with st.spinner("Rendering PNG locally…"):
                image = figure.to_image(format="png", scale=2)
            st.session_state[state_key] = (signature, image)
        except Exception as error:
            st.session_state.pop(state_key, None)
            st.warning(f"PNG export is unavailable: {error}")
    cached = st.session_state.get(state_key)
    if cached is not None and cached[0] == signature:
        st.download_button(
            f"Download {kind} PNG",
            cached[1],
            file_name=safe_filename(source, kind, "png"),
            mime="image/png",
            on_click="ignore",
        )


def tracks_csv(tracks: AcousticTracks) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("time_seconds", "f0_hz", "voiced", "rms_energy", "spectral_centroid_hz"))
    for time, f0, voiced, rms, centroid in zip(
        tracks.times, tracks.f0_hz, tracks.voiced, tracks.rms, tracks.centroid_hz, strict=True
    ):
        writer.writerow((f"{time:.6f}", "" if np.isnan(f0) else f"{f0:.4f}", bool(voiced), f"{rms:.8f}", f"{centroid:.4f}"))
    return output.getvalue()


def metadata(recording: AudioRecording, processed: ProcessingResult) -> None:
    columns = st.columns(5)
    columns[0].metric("Filename", recording.name)
    columns[1].metric("Duration", f"{recording.duration:.2f} s")
    columns[2].metric("Original rate", f"{recording.sample_rate:,} Hz")
    columns[3].metric("Channels", recording.channels)
    columns[4].metric("Processed rate", f"{processed.sample_rate:,} Hz")


def collect_recordings() -> dict[str, AudioRecording]:
    recordings: dict[str, AudioRecording] = {}
    uploads = st.sidebar.file_uploader(
        "Upload one or more recordings",
        type=list(SUPPORTED_EXTENSIONS),
        accept_multiple_files=True,
        help="Audio stays in this local Streamlit process.",
    )
    for upload in uploads:
        try:
            recording = decode_audio(upload.getvalue(), upload.name)
            recordings[f"Upload · {upload.name}"] = recording
        except AudioLoadError as error:
            st.sidebar.error(str(error))

    microphone = st.sidebar.audio_input("Record with your microphone")
    if microphone is not None:
        try:
            recording = decode_audio(microphone.getvalue(), "microphone-recording.wav")
            recordings["Microphone recording"] = recording
        except AudioLoadError as error:
            st.sidebar.error(str(error))

    demo_names = st.sidebar.multiselect(
        "Add generated demonstrations",
        ("440 Hz sine wave", "Fundamental and harmonics", "Short noise burst", "Rising frequency sweep"),
        help="Synthetic examples are separate from real speech recordings.",
    )
    for demo_name in demo_names:
        recordings[f"Demo · {demo_name}"] = generate_demo(demo_name)
    return recordings


st.title("Voice Spectrogram Playground")
st.caption("Explore speech locally through waveforms, spectra, spectrograms, pitch, and energy.")
st.sidebar.header("Audio input")
recordings = collect_recordings()

if not recordings:
    st.info("Upload or record audio, or add a generated demonstration from the sidebar to begin.")
    st.markdown("### Quick start\nChoose **440 Hz sine wave** to see one clear peak in the spectrum and one horizontal line in the spectrogram.")
    st.stop()

active_label = st.sidebar.selectbox("Active recording", tuple(recordings))
active = recordings[active_label]

st.sidebar.header("Preprocessing")
convert_mono = st.sidebar.checkbox(
    "Average stereo to mono",
    value=True,
    help="When disabled, analysis uses the first channel instead of averaging channels.",
)
enable_resampling = st.sidebar.checkbox("Resample", value=True)
target_rate = st.sidebar.selectbox("Processed sample rate", (8_000, 16_000, 22_050, 44_100, 48_000), index=1, disabled=not enable_resampling)
trim = st.sidebar.checkbox("Trim leading/trailing silence", value=False)
normalization = st.sidebar.selectbox("Amplitude normalization", ("None", "Peak", "RMS"))

processed_recordings = {
    label: process_recording(
        recording.samples,
        recording.sample_rate,
        convert_mono,
        target_rate if enable_resampling else None,
        trim,
        normalization,
    )
    for label, recording in recordings.items()
}
processed = processed_recordings[active_label]

st.sidebar.header("Analysis")
window_ms = st.sidebar.slider("Window duration (ms)", 10.0, 80.0, DEFAULTS.window_ms, 1.0)
hop_ms = st.sidebar.slider("Hop duration (ms)", 2.0, 30.0, DEFAULTS.hop_ms, 1.0)
minimum_fft_size = round(processed.sample_rate * window_ms / 1_000)
fft_options = tuple(size for size in (128, 256, 512, 1_024, 2_048, 4_096) if size >= minimum_fft_size)
n_fft = st.sidebar.select_slider(
    "FFT size",
    options=fft_options,
    value=DEFAULTS.n_fft if DEFAULTS.n_fft in fft_options else fft_options[0],
)
nyquist = processed.sample_rate / 2
frequency_range = st.sidebar.slider(
    "Visible frequency range (Hz)",
    0.0,
    float(nyquist),
    (0.0, float(min(DEFAULTS.max_frequency_hz, nyquist))),
    step=max(10.0, nyquist / 800),
)
dynamic_range = st.sidebar.slider("Dynamic range (dB)", 20.0, 120.0, DEFAULTS.dynamic_range_db, 5.0)
color_map = st.sidebar.selectbox("Color map", COLORMAPS, index=1)
show_f0 = st.sidebar.checkbox("Overlay F0", value=True)
show_centroid = st.sidebar.checkbox("Overlay spectral centroid", value=False)
show_formants = st.sidebar.checkbox("Overlay estimated formants", value=False)

metadata(active, processed)
st.audio(audio_to_wav_bytes(processed.samples, processed.sample_rate), format="audio/wav")

selection_step = max(0.001, min(0.01, processed.duration / 100))
start_seconds, end_seconds = st.slider(
    "Region to analyze (seconds)",
    min_value=0.0,
    max_value=float(max(processed.duration, selection_step)),
    value=(0.0, float(processed.duration)),
    step=selection_step,
)
region = select_region(processed.samples, processed.sample_rate, start_seconds, end_seconds)
st.caption(f"Selected {len(region) / processed.sample_rate:.3f} seconds")
st.audio(audio_to_wav_bytes(region, processed.sample_rate), format="audio/wav")

with st.spinner("Analyzing selected region…"):
    linear = cached_linear(region, processed.sample_rate, window_ms, hop_ms, n_fft, dynamic_range)
    tracks = cached_tracks(region, processed.sample_rate, window_ms, hop_ms)
    formants = (
        estimate_formants(region, processed.sample_rate, window_ms=window_ms, hop_ms=hop_ms)
        if show_formants
        else None
    )

wave_tab, spectrum_tab, linear_tab, mel_tab, tracks_tab, comparison_tab, guide_tab = st.tabs(
    ("Waveform", "Fourier spectrum", "Linear spectrogram", "Mel spectrogram", "Acoustic tracks", "Compare", "Guide")
)

with wave_tab:
    show_wave_rms = st.checkbox("Show RMS contour on waveform", value=False)
    wave_figure = waveform_figure(
        processed.samples,
        processed.sample_rate,
        selected_start=start_seconds,
        selected_end=end_seconds,
        show_rms=show_wave_rms,
    )
    st.plotly_chart(wave_figure, width="stretch")
    st.caption(WAVEFORM)
    png_download(wave_figure, active.name, "waveform")

with spectrum_tab:
    spectrum_controls = st.columns(2)
    spectrum_display = spectrum_controls[0].selectbox("Spectrum units", ("Decibels", "Magnitude", "Power"))
    log_frequency = spectrum_controls[1].checkbox("Logarithmic frequency axis")
    spectrum = calculate_spectrum(
        region,
        processed.sample_rate,
        long_threshold_seconds=DEFAULTS.long_spectrum_seconds,
        window_ms=window_ms,
        hop_ms=hop_ms,
        n_fft=n_fft,
    )
    spectrum_plot = spectrum_figure(
        spectrum,
        display=spectrum_display,
        logarithmic_frequency=log_frequency,
        min_frequency=frequency_range[0],
        max_frequency=frequency_range[1],
    )
    st.plotly_chart(spectrum_plot, width="stretch")
    st.caption(SPECTRUM)

with linear_tab:
    linear_figure = spectrogram_figure(
        linear,
        min_frequency=frequency_range[0],
        max_frequency=frequency_range[1],
        dynamic_range_db=dynamic_range,
        color_map=color_map,
        tracks=tracks,
        show_f0=show_f0,
        show_centroid=show_centroid,
        formants=formants,
    )
    st.plotly_chart(linear_figure, width="stretch")
    st.caption(LINEAR_SPECTROGRAM)
    if show_formants:
        st.warning(FORMANTS)
    png_download(linear_figure, active.name, "linear-spectrogram")

with mel_tab:
    mel_bands = st.slider("Mel bands", 20, 256, DEFAULTS.mel_bands, 4)
    mel = mel_spectrogram(
        region,
        processed.sample_rate,
        window_ms=window_ms,
        hop_ms=hop_ms,
        n_fft=n_fft,
        n_mels=mel_bands,
        fmin=frequency_range[0],
        fmax=frequency_range[1],
        dynamic_range_db=dynamic_range,
    )
    mel_figure = spectrogram_figure(
        mel,
        min_frequency=frequency_range[0],
        max_frequency=frequency_range[1],
        dynamic_range_db=dynamic_range,
        color_map=color_map,
        title="Log-Mel spectrogram",
    )
    st.plotly_chart(mel_figure, width="stretch")
    st.caption(MEL_SPECTROGRAM)

with tracks_tab:
    st.plotly_chart(track_figure(tracks), width="stretch")
    st.caption("Unvoiced frames have missing F0 values; they are not plotted as artificial 0 Hz lines.")
    st.download_button(
        "Download F0 and energy CSV",
        tracks_csv(tracks),
        file_name=safe_filename(active.name, "acoustic-tracks", "csv"),
        mime="text/csv",
    )

with comparison_tab:
    if len(recordings) < 2:
        st.info("Add at least two recordings or demonstrations to use comparison view.")
    else:
        default_comparison = next(label for label in recordings if label != active_label)
        compare_label = st.selectbox("Second recording", tuple(recordings), index=tuple(recordings).index(default_comparison))
        compare_processed = processed_recordings[compare_label]
        shared_duration = max(processed.duration, compare_processed.duration)
        shared_max_frequency = min(
            frequency_range[1], processed.sample_rate / 2, compare_processed.sample_rate / 2
        )
        left_spectrogram = cached_linear(processed.samples, processed.sample_rate, window_ms, hop_ms, n_fft, dynamic_range)
        right_spectrogram = cached_linear(
            compare_processed.samples,
            compare_processed.sample_rate,
            window_ms,
            hop_ms,
            n_fft,
            dynamic_range,
        )
        columns = st.columns(2)
        for column, label, recording, value, result in (
            (columns[0], active_label, active, processed, left_spectrogram),
            (columns[1], compare_label, recordings[compare_label], compare_processed, right_spectrogram),
        ):
            with column:
                st.subheader(label)
                st.audio(audio_to_wav_bytes(value.samples, value.sample_rate), format="audio/wav")
                figure = spectrogram_figure(
                    result,
                    min_frequency=frequency_range[0],
                    max_frequency=shared_max_frequency,
                    dynamic_range_db=dynamic_range,
                    color_map=color_map,
                    title=recording.name,
                    x_range=(0, shared_duration),
                )
                st.plotly_chart(figure, width="stretch")
        st.warning(COMPARISON_WARNING)

with guide_tab:
    st.markdown(
        """
### What to look for

- **Voiced sounds:** repeating waveform cycles, harmonic stripes, and a defined F0 track.
- **Vowels:** sustained voicing with broad bands of stronger energy related to vocal-tract resonances.
- **Fricatives:** irregular waveform energy and diffuse spectrogram energy, often at higher frequencies.
- **Stops:** a low-energy closure followed by a short broadband release burst.
- **Silence/unvoiced regions:** F0 is intentionally absent because no reliable periodic fundamental exists.

Zoom and hover in every Plotly chart. Change the selected region to compare a stable vowel with a consonant or transition.
"""
    )

settings = {
    "source": active.name,
    "preprocessing": {
        "average_stereo_to_mono": convert_mono,
        "resample": enable_resampling,
        "processed_sample_rate": processed.sample_rate,
        "trim_silence": trim,
        "normalization": normalization,
    },
    "selection_seconds": {"start": start_seconds, "end": end_seconds},
    "analysis": {
        "window_ms": window_ms,
        "hop_ms": hop_ms,
        "n_fft": n_fft,
        "frequency_range_hz": list(frequency_range),
        "dynamic_range_db": dynamic_range,
        "color_map": color_map,
        "overlay_f0": show_f0,
        "overlay_centroid": show_centroid,
        "overlay_estimated_formants": show_formants,
    },
}
st.sidebar.download_button(
    "Download analysis settings",
    json.dumps(settings, indent=2),
    file_name=safe_filename(active.name, "analysis-settings", "json"),
    mime="application/json",
)
st.sidebar.caption("All decoding and analysis run locally. No audio is sent to an external service.")
