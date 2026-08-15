"""Local audio decoding, metadata, and WAV encoding."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import tempfile

import librosa
import numpy as np
import soundfile as sf


class AudioLoadError(ValueError):
    """Raised when uploaded bytes cannot be decoded as audio."""


@dataclass(frozen=True)
class AudioRecording:
    """Decoded audio in its original sample rate and channel layout."""

    name: str
    samples: np.ndarray
    sample_rate: int
    channels: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


def _channels_last(samples: np.ndarray) -> np.ndarray:
    """Return audio as frames or frames-by-channels."""

    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 2 and values.shape[0] <= 8 and values.shape[1] > values.shape[0]:
        values = values.T
    if values.ndim not in (1, 2):
        raise AudioLoadError(f"Expected mono or multichannel audio, got shape {values.shape}.")
    return values


def load_audio_bytes(data: bytes, filename: str) -> AudioRecording:
    """Decode an audio upload without sending it outside the local process."""

    if not data:
        raise AudioLoadError("The audio file is empty.")

    suffix = Path(filename).suffix.lower() or ".audio"
    try:
        samples, sample_rate = sf.read(BytesIO(data), dtype="float32", always_2d=False)
    except Exception as soundfile_error:
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
                temporary.write(data)
                temporary.flush()
                decoded, sample_rate = librosa.load(temporary.name, sr=None, mono=False)
            samples = decoded.T if np.asarray(decoded).ndim == 2 else decoded
        except Exception as fallback_error:
            ffmpeg_hint = (
                " This format may require FFmpeg. Install FFmpeg and make sure its executable "
                "is on PATH, or convert the recording to WAV, FLAC, OGG, or MP3."
                if suffix not in {".wav", ".flac", ".ogg", ".mp3"}
                else " Verify that the file is valid and uses a codec supported by libsndfile or FFmpeg."
            )
            raise AudioLoadError(
                f"Could not decode {filename}.{ffmpeg_hint} Decoder details: {fallback_error}"
            ) from soundfile_error

    values = _channels_last(np.asarray(samples))
    if not np.isfinite(values).all() or values.size == 0 or sample_rate <= 0:
        raise AudioLoadError(f"{filename} did not contain usable finite audio samples.")
    channels = 1 if values.ndim == 1 else values.shape[1]
    return AudioRecording(filename, values, int(sample_rate), channels)


def audio_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode floating-point audio for playback or download."""

    output = BytesIO()
    sf.write(output, np.asarray(samples, dtype=np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()

