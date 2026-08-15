"""Small deterministic signals that expose basic spectral behavior."""

from __future__ import annotations

import numpy as np

from .audio_io import AudioRecording


def generate_demo(name: str, sample_rate: int = 16_000, duration: float = 2.0) -> AudioRecording:
    """Generate one of the built-in demonstrations."""

    time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
    if name == "440 Hz sine wave":
        samples = 0.6 * np.sin(2 * np.pi * 440 * time)
    elif name == "Fundamental and harmonics":
        samples = sum(
            (0.6 / harmonic) * np.sin(2 * np.pi * 180 * harmonic * time)
            for harmonic in range(1, 6)
        )
    elif name == "Short noise burst":
        rng = np.random.default_rng(7)
        samples = np.zeros_like(time)
        active = (time >= 0.7) & (time < 1.0)
        samples[active] = 0.35 * rng.standard_normal(int(np.sum(active)))
    elif name == "Rising frequency sweep":
        start_hz, end_hz = 150.0, 2_500.0
        slope = (end_hz - start_hz) / duration
        phase = 2 * np.pi * (start_hz * time + 0.5 * slope * time**2)
        samples = 0.6 * np.sin(phase)
    else:
        raise ValueError(f"Unknown demonstration: {name}")
    return AudioRecording(f"demo-{name.lower().replace(' ', '-')}.wav", samples.astype(np.float32), sample_rate, 1)

