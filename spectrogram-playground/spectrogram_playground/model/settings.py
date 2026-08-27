from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class VisualizationTab(StrEnum):
    WAVEFORM = "waveform"
    SPECTROGRAM = "spectrogram"
    FOURIER = "fourier"


class ToolMode(StrEnum):
    SEEK = "seek"
    SELECT = "select"
    PAN = "pan"


@dataclass(slots=True)
class AnalysisSettings:
    analysis_rate: int = 16_000
    window_ms: float = 25.0
    hop_ms: float = 10.0
    n_fft: int = 512
    dynamic_range_db: float = 80.0
    frequency_mode: str = "linear"
    fmin: float = 0.0
    fmax: float = 8_000.0
    n_mels: int = 80
    show_f0: bool = False
    show_rms: bool = False
    show_formants: bool = False
    spectrum_scale: str = "decibel"
    logarithmic_frequency: bool = False
    spectrum_layout: str = "overlay"
    spectrum_window: str = "hann"
    default_spectrum_window_seconds: float = 0.25

    def cache_key(self) -> tuple[object, ...]:
        return tuple(asdict(self).items())
