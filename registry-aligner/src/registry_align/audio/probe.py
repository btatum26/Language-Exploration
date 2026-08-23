"""FFprobe adapter and source hashing."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from registry_align.errors import DependencyError, ProcessingError


class AudioProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    codec: str
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration_s: float = Field(gt=0)
    frame_count: int | None = Field(default=None, gt=0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, check=False, timeout=15
        )
    except (FileNotFoundError, OSError) as exc:
        raise DependencyError(f"required executable is unavailable: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DependencyError(f"timed out checking executable: {executable}") from exc
    if completed.returncode != 0:
        raise DependencyError(f"cannot query {executable} version: {completed.stderr.strip()}")
    first_line = (completed.stdout or completed.stderr).splitlines()
    return first_line[0].strip() if first_line else "unknown"


def probe_audio(path: Path, executable: str) -> AudioProbe:
    command = [
        executable,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,duration,nb_frames,duration_ts:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    except (FileNotFoundError, OSError) as exc:
        raise DependencyError(f"required FFprobe executable is unavailable: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError(f"FFprobe timed out for {path}") from exc
    if completed.returncode != 0:
        raise ProcessingError(f"FFprobe could not decode {path}: {completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        duration_value = stream.get("duration") or payload.get("format", {}).get("duration")
        sample_rate = int(stream["sample_rate"])
        duration = float(duration_value)
        frame_value = stream.get("nb_frames") or stream.get("duration_ts")
        frame_count = int(frame_value) if frame_value not in (None, "N/A") else None
        return AudioProbe(
            codec=str(stream["codec_name"]),
            sample_rate_hz=sample_rate,
            channels=int(stream["channels"]),
            duration_s=duration,
            frame_count=frame_count,
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProcessingError(f"FFprobe returned incomplete audio metadata for {path}") from exc
