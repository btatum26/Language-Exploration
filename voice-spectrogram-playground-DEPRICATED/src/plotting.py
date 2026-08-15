"""Plotly figure construction with no Streamlit dependencies."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .spectral_analysis import AcousticTracks, SpectrogramResult, SpectrumResult


def waveform_figure(
    samples: np.ndarray,
    sample_rate: int,
    *,
    selected_start: float | None = None,
    selected_end: float | None = None,
    show_rms: bool = False,
    title: str = "Waveform",
) -> go.Figure:
    """Create an interactive waveform with an optional selection and RMS contour."""

    values = np.asarray(samples).reshape(-1)
    stride = max(1, len(values) // 100_000)
    indices = np.arange(0, len(values), stride)
    times = indices / sample_rate
    figure = go.Figure()
    figure.add_trace(go.Scattergl(x=times, y=values[indices], name="Amplitude", line={"width": 1}))
    figure.add_hline(y=0, line_width=1, line_color="gray")
    if selected_start is not None and selected_end is not None:
        figure.add_vrect(
            x0=selected_start,
            x1=selected_end,
            fillcolor="gold",
            opacity=0.18,
            line_width=1,
            line_color="goldenrod",
            annotation_text="Selected region",
        )
    if show_rms and len(values):
        window = max(1, round(0.025 * sample_rate))
        kernel = np.ones(window) / window
        rms = np.sqrt(np.convolve(np.square(values), kernel, mode="same"))
        figure.add_trace(go.Scattergl(x=times, y=rms[indices], name="RMS energy", line={"width": 2}))
    figure.update_layout(title=title, xaxis_title="Time (seconds)", yaxis_title="Amplitude", hovermode="x unified")
    return figure


def spectrum_figure(
    result: SpectrumResult,
    *,
    display: str = "Decibels",
    logarithmic_frequency: bool = False,
    min_frequency: float = 0.0,
    max_frequency: float = 8_000.0,
) -> go.Figure:
    """Create a spectrum in magnitude, power, or relative dB units."""

    if display == "Magnitude":
        values, label = result.magnitude, "Magnitude"
    elif display == "Power":
        values, label = result.power, "Power"
    else:
        reference = max(float(np.max(result.power, initial=0)), np.finfo(float).tiny)
        values = 10 * np.log10(np.maximum(result.power, reference * 1e-10) / reference)
        label = "Relative level (dB)"
    mask = (result.frequencies >= min_frequency) & (result.frequencies <= max_frequency)
    if logarithmic_frequency:
        mask &= result.frequencies > 0
    figure = go.Figure(go.Scatter(x=result.frequencies[mask], y=np.asarray(values)[mask], mode="lines"))
    figure.update_layout(
        title=result.method,
        xaxis_title="Frequency (Hz)",
        yaxis_title=label,
        xaxis_type="log" if logarithmic_frequency else "linear",
        hovermode="x unified",
    )
    return figure


def spectrogram_figure(
    result: SpectrogramResult,
    *,
    min_frequency: float = 0.0,
    max_frequency: float = 8_000.0,
    dynamic_range_db: float = 80.0,
    color_map: str = "Magma",
    tracks: AcousticTracks | None = None,
    show_f0: bool = False,
    show_centroid: bool = False,
    formants: tuple[np.ndarray, np.ndarray] | None = None,
    title: str = "Linear-frequency spectrogram",
    x_range: tuple[float, float] | None = None,
) -> go.Figure:
    """Create a heatmap and optional acoustic overlays."""

    mask = (result.frequencies >= min_frequency) & (result.frequencies <= max_frequency)
    figure = go.Figure(
        go.Heatmap(
            x=result.times,
            y=result.frequencies[mask],
            z=result.decibels[mask],
            zmin=-dynamic_range_db,
            zmax=0,
            colorscale=color_map,
            colorbar={"title": "dB"},
            hovertemplate="Time %{x:.3f} s<br>Frequency %{y:.0f} Hz<br>%{z:.1f} dB<extra></extra>",
        )
    )
    if tracks is not None and show_f0:
        figure.add_trace(
            go.Scatter(x=tracks.times, y=tracks.f0_hz, name="F0 (voiced)", line={"color": "cyan", "width": 2})
        )
    if tracks is not None and show_centroid:
        figure.add_trace(
            go.Scatter(x=tracks.times, y=tracks.centroid_hz, name="Spectral centroid", line={"color": "white", "width": 1})
        )
    if formants is not None:
        times, estimates = formants
        for index, color in enumerate(("lime", "yellow", "orange")):
            figure.add_trace(
                go.Scatter(
                    x=times,
                    y=estimates[index],
                    name=f"Estimated F{index + 1}",
                    mode="markers",
                    marker={"color": color, "size": 3},
                )
            )
    figure.update_layout(title=title, xaxis_title="Time (seconds)", yaxis_title="Frequency (Hz)")
    if x_range is not None:
        figure.update_xaxes(range=list(x_range))
    return figure


def track_figure(tracks: AcousticTracks) -> go.Figure:
    """Plot F0, RMS, centroid, and explicit voiced/unvoiced state."""

    figure = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.06)
    figure.add_trace(go.Scatter(x=tracks.times, y=tracks.f0_hz, name="F0 (Hz)"), row=1, col=1)
    figure.add_trace(go.Scatter(x=tracks.times, y=tracks.rms, name="RMS energy"), row=2, col=1)
    figure.add_trace(go.Scatter(x=tracks.times, y=tracks.centroid_hz, name="Centroid (Hz)"), row=3, col=1)
    figure.add_trace(
        go.Scatter(
            x=tracks.times,
            y=tracks.voiced.astype(int),
            name="Voicing",
            line_shape="hv",
            fill="tozeroy",
        ),
        row=4,
        col=1,
    )
    figure.update_yaxes(title_text="F0 (Hz)", row=1, col=1)
    figure.update_yaxes(title_text="RMS", row=2, col=1)
    figure.update_yaxes(title_text="Hz", row=3, col=1)
    figure.update_yaxes(
        title_text="Voicing",
        tickmode="array",
        tickvals=[0, 1],
        ticktext=["Unvoiced", "Voiced"],
        range=[-0.05, 1.05],
        row=4,
        col=1,
    )
    figure.update_xaxes(title_text="Time (seconds)", row=4, col=1)
    figure.update_layout(height=700, hovermode="x unified")
    return figure
