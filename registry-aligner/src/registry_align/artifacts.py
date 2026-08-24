"""Disposable prepared-audio working files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from registry_align.audio.decoder import prepare_recording
from registry_align.audio.probe import executable_version
from registry_align.config import AudioConfig
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry


class ArtifactStore(Protocol):
    def prepare(
        self,
        entry: RegistryEntry,
        config: AudioConfig,
        workspace: Path,
    ) -> PreparedRecording: ...

    def status(self) -> dict[str, Any]: ...


def preparation_fingerprint(config: AudioConfig, ffmpeg_version: str) -> str:
    payload = {
        "implementation": "registry-align-audio",
        "version": config.preparation_version,
        "canonical": "native-rate-pcm-s16le",
        "alignment_sample_rate_hz": config.alignment_sample_rate_hz,
        "alignment_channels": config.alignment_channels,
        "ffmpeg_version": ffmpeg_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class LocalArtifactStore:
    """Creates run-scoped WAVs; the service removes the workspace after each run."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)

    def prepare(
        self,
        entry: RegistryEntry,
        config: AudioConfig,
        workspace: Path,
    ) -> PreparedRecording:
        ffmpeg_version = executable_version(config.ffmpeg_executable)
        profile = preparation_fingerprint(config, ffmpeg_version)
        entry_key = hashlib.sha256(entry.id.encode("utf-8")).hexdigest()[:24]
        directory = workspace / "prepared" / entry_key
        prepared = prepare_recording(
            entry,
            directory,
            config,
            ffmpeg_version=ffmpeg_version,
        ).model_copy(update={"preparation_fingerprint": profile})
        return prepared.model_copy(update={"preparation_fingerprint": profile})

    def status(self) -> dict[str, Any]:
        files = (
            [path for path in self.root.rglob("*") if path.is_file()] if self.root.exists() else []
        )
        return {
            "root": str(self.root),
            "working_files": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "durable": False,
        }
