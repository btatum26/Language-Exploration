"""Central defaults used by the interface and signal-processing modules."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AnalysisDefaults:
    """Understandable speech-analysis defaults, expressed in user-facing units."""

    processed_sample_rate: int = 16_000
    window_ms: float = 25.0
    hop_ms: float = 10.0
    n_fft: int = 512
    dynamic_range_db: float = 80.0
    min_frequency_hz: float = 0.0
    max_frequency_hz: float = 8_000.0
    mel_bands: int = 80
    pitch_min_hz: float = 65.0
    pitch_max_hz: float = 500.0
    long_spectrum_seconds: float = 1.0
    silence_top_db: float = 40.0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


DEFAULTS = AnalysisDefaults()
SUPPORTED_EXTENSIONS = ("wav", "flac", "ogg", "mp3", "m4a")
COLORMAPS = ("Viridis", "Magma", "Inferno", "Cividis", "Turbo", "Plasma")

