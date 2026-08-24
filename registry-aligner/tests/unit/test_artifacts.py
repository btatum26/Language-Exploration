from __future__ import annotations

from pathlib import Path
from typing import Any

from registry_align.artifacts import LocalArtifactStore
from registry_align.config import AudioConfig
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry


def _entry(tmp_path: Path) -> RegistryEntry:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source bytes")
    return RegistryEntry(
        id="one",
        source_entry_index=0,
        source_registry_path=tmp_path / "registry.json",
        audio_relative_path="source.wav",
        audio_resolved_path=source,
        transcript_raw="ciao",
        language="it",
    )


def test_prepared_audio_is_created_inside_run_workspace(tmp_path: Path, monkeypatch: Any) -> None:
    destinations: list[Path] = []

    def prepare(
        entry: RegistryEntry, destination: Path, config: AudioConfig, **kwargs: Any
    ) -> PreparedRecording:
        del config, kwargs
        destinations.append(destination)
        destination.mkdir(parents=True)
        canonical = destination / "canonical.wav"
        alignment = destination / "alignment.wav"
        canonical.write_bytes(b"canonical")
        alignment.write_bytes(b"alignment")
        from registry_align.audio.probe import sha256_file

        return PreparedRecording(
            recording_id=entry.id,
            source_audio_sha256=sha256_file(entry.audio_resolved_path),
            source_codec="pcm_s16le",
            source_sample_rate_hz=44100,
            source_channels=1,
            source_duration_s=1.0,
            source_frame_count=44100,
            canonical_pcm_path=canonical,
            canonical_pcm_sha256=sha256_file(canonical),
            canonical_sample_rate_hz=44100,
            canonical_channels=1,
            canonical_frame_count=44100,
            alignment_audio_path=alignment,
            alignment_audio_sha256=sha256_file(alignment),
            alignment_sample_rate_hz=16000,
            alignment_channels=1,
            decoder_name="fake",
            decoder_version="1",
        )

    monkeypatch.setattr("registry_align.artifacts.prepare_recording", prepare)
    monkeypatch.setattr("registry_align.artifacts.executable_version", lambda value: "ffmpeg 1")
    root = tmp_path / "working"
    workspace = root / "work" / "run-id"
    prepared = LocalArtifactStore(root).prepare(_entry(tmp_path), AudioConfig(), workspace)

    assert destinations[0].is_relative_to(workspace)
    assert prepared.canonical_pcm_path.is_file()
    assert prepared.preparation_fingerprint != "unspecified"


def test_working_store_does_not_reuse_files_across_runs(tmp_path: Path, monkeypatch: Any) -> None:
    calls = 0

    def prepare(
        entry: RegistryEntry, destination: Path, config: AudioConfig, **kwargs: Any
    ) -> PreparedRecording:
        nonlocal calls
        del config, kwargs
        calls += 1
        destination.mkdir(parents=True)
        canonical = destination / "canonical.wav"
        alignment = destination / "alignment.wav"
        canonical.write_bytes(f"canonical-{calls}".encode())
        alignment.write_bytes(f"alignment-{calls}".encode())
        from registry_align.audio.probe import sha256_file

        return PreparedRecording(
            recording_id=entry.id,
            source_audio_sha256=sha256_file(entry.audio_resolved_path),
            source_codec="pcm_s16le",
            source_sample_rate_hz=16000,
            source_channels=1,
            source_duration_s=1.0,
            canonical_pcm_path=canonical,
            canonical_pcm_sha256=sha256_file(canonical),
            canonical_sample_rate_hz=16000,
            canonical_channels=1,
            canonical_frame_count=16000,
            alignment_audio_path=alignment,
            alignment_audio_sha256=sha256_file(alignment),
            alignment_sample_rate_hz=16000,
            alignment_channels=1,
            decoder_name="fake",
            decoder_version="1",
        )

    monkeypatch.setattr("registry_align.artifacts.prepare_recording", prepare)
    monkeypatch.setattr("registry_align.artifacts.executable_version", lambda value: "ffmpeg 1")
    store = LocalArtifactStore(tmp_path / "working")
    item = _entry(tmp_path)

    first = store.prepare(item, AudioConfig(), tmp_path / "working" / "work" / "one")
    second = store.prepare(item, AudioConfig(), tmp_path / "working" / "work" / "two")

    assert calls == 2
    assert first.canonical_pcm_path != second.canonical_pcm_path
