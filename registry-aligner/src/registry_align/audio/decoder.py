"""FFmpeg-based canonical and alignment WAV generation."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from registry_align.audio.probe import executable_version, probe_audio, sha256_file
from registry_align.config import AudioConfig
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.errors import DependencyError, ProcessingError


def _run_ffmpeg(command: list[str], destination: Path) -> None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise DependencyError(f"required FFmpeg executable is unavailable: {command[0]}") from exc
    if completed.returncode != 0:
        raise ProcessingError(
            f"FFmpeg failed creating {destination.name}: {completed.stderr.strip()}"
        )


def _atomic_ffmpeg(destination: Path, command_prefix: list[str], output_args: list[str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tmp.wav")
    try:
        _run_ffmpeg([*command_prefix, *output_args, str(temporary)], destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_recording(
    entry: RegistryEntry,
    output_directory: Path,
    config: AudioConfig,
    *,
    ffmpeg_version: str | None = None,
) -> PreparedRecording:
    source_probe = probe_audio(entry.audio_resolved_path, config.ffprobe_executable)
    source_hash = sha256_file(entry.audio_resolved_path)
    safe_name = f"{entry.source_entry_index:06d}-{source_hash[:16]}"
    canonical_path = output_directory / "derived" / "pcm" / f"{safe_name}.wav"
    alignment_path = output_directory / "derived" / "alignment-audio" / f"{safe_name}.wav"
    prefix = [
        config.ffmpeg_executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(entry.audio_resolved_path),
        "-map",
        "0:a:0",
    ]
    _atomic_ffmpeg(canonical_path, prefix, ["-c:a", "pcm_s16le"])
    _atomic_ffmpeg(
        alignment_path,
        prefix,
        [
            "-ac",
            str(config.alignment_channels),
            "-ar",
            str(config.alignment_sample_rate_hz),
            "-c:a",
            "pcm_s16le",
        ],
    )
    canonical_probe = probe_audio(canonical_path, config.ffprobe_executable)
    frame_count = canonical_probe.frame_count or round(
        canonical_probe.duration_s * canonical_probe.sample_rate_hz
    )
    return PreparedRecording(
        recording_id=entry.id,
        source_audio_sha256=source_hash,
        source_codec=source_probe.codec,
        source_sample_rate_hz=source_probe.sample_rate_hz,
        source_channels=source_probe.channels,
        source_duration_s=source_probe.duration_s,
        canonical_pcm_path=canonical_path,
        canonical_pcm_sha256=sha256_file(canonical_path),
        canonical_sample_rate_hz=canonical_probe.sample_rate_hz,
        canonical_channels=canonical_probe.channels,
        canonical_frame_count=frame_count,
        alignment_audio_path=alignment_path,
        alignment_audio_sha256=sha256_file(alignment_path),
        alignment_sample_rate_hz=config.alignment_sample_rate_hz,
        alignment_channels=config.alignment_channels,
        decoder_name="ffmpeg",
        decoder_version=ffmpeg_version or executable_version(config.ffmpeg_executable),
    )
