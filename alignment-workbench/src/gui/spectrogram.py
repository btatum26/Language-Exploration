"""Bounded, viewport-local spectral preview for the desktop audio view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from PySide6 import QtGui


@dataclass(frozen=True, slots=True)
class SpectrogramPreview:
    image: QtGui.QImage
    start_sample: int
    end_sample: int
    maximum_frequency_hz: float


def load_spectrogram(
    path: Path, start: int, end: int, *, maximum_columns: int = 1_200
) -> SpectrogramPreview:
    """Sample Hann-windowed spectra at up to 1,200 viewport time positions.

    Long views use spaced windows; zooming recomputes them at finer time resolution.
    Neither the whole recording nor an unbounded STFT is retained. Channel powers
    are averaged so opposite-phase stereo channels do not cancel.
    """
    with sf.SoundFile(path) as source:
        frames, rate = len(source), int(source.samplerate)
        if frames <= 0 or rate <= 0 or source.channels <= 0:
            raise ValueError("spectrogram requires nonempty audio")
        start = max(0, min(start, frames - 1))
        end = max(start + 1, min(end, frames))
        window_size = min(4_096, max(32, round(rate * 0.025)))
        fft_size = 1 << (window_size - 1).bit_length()
        window = np.hanning(window_size)
        columns = max(1, min(maximum_columns, int(np.ceil((end - start) / (rate * 0.01)))))
        pixels = np.empty((fft_size // 2 + 1, columns), dtype=np.uint8)
        cache_start = 0
        cache = np.empty((0, source.channels), dtype=np.float32)
        # Decode nearby windows together. MP3 seeks need decoder preroll for the
        # bit reservoir; repeatedly seeking overlapping FFT windows loses it.
        preroll = rate // 4 if source.format == "MP3" else 0
        for column in range(columns):
            center = start + int((column + 0.5) * (end - start) / columns)
            left = center - window_size // 2
            read_start, read_end = max(0, left), min(frames, left + window_size)
            if read_start < cache_start or read_end > cache_start + len(cache):
                cache_start = max(0, read_start - preroll)
                source.seek(cache_start)
                cache = source.read(
                    max(65_536, read_end - cache_start), dtype="float32", always_2d=True
                )
            block = cache[read_start - cache_start : read_end - cache_start]
            padded = np.zeros((window_size, source.channels), dtype=np.float32)
            offset = read_start - left
            padded[offset : offset + len(block)] = block
            spectrum = np.fft.rfft(padded * window[:, None], n=fft_size, axis=0)
            power = np.mean(np.abs(spectrum * (2 / window.sum())) ** 2, axis=1)
            db = 10 * np.log10(np.maximum(power, 1e-12))
            pixels[:, column] = np.clip((db + 90) * (255 / 90), 0, 255).astype(np.uint8)

    # QImage owns a copy; no NumPy buffer survives through a borrowed pointer.
    pixels = np.ascontiguousarray(pixels[::-1])
    image = QtGui.QImage(
        pixels.data, columns, len(pixels), columns, QtGui.QImage.Format.Format_Indexed8
    ).copy()
    anchors = np.array([[10, 8, 25], [65, 20, 100], [170, 45, 85], [240, 125, 35], [252, 250, 170]])
    palette = np.stack(
        [np.interp(np.linspace(0, 4, 256), np.arange(5), anchors[:, c]) for c in range(3)],
        axis=1,
    ).astype(np.uint8)
    image.setColorTable([QtGui.qRgb(int(r), int(g), int(b)) for r, g, b in palette])
    return SpectrogramPreview(image, start, end, rate / 2)
