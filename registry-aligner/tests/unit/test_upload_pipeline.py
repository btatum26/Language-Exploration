from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from registry_align.audio.probe import sha256_file
from registry_align.config import AppConfig, RemoteAudioConfig
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.pipeline.service import AlignmentService
from registry_align.remote_audio import (
    AudioUploadRequest,
    RemoteAudioObject,
    storage_key_for_sha256,
)


def _entry(tmp_path: Path, index: int) -> RegistryEntry:
    source = tmp_path / f"source-{index}.wav"
    source.write_bytes(f"audio-{index}".encode())
    return RegistryEntry(
        id=f"recording-{index}",
        source_entry_index=index,
        source_registry_path=tmp_path / "registry.json",
        audio_relative_path=source.name,
        audio_resolved_path=source,
        transcript_raw="ciao",
        language="it",
    )


class PreparingStore:
    def __init__(self, third_prepared: Event) -> None:
        self.third_prepared = third_prepared

    def prepare(self, entry: RegistryEntry, config: Any, workspace: Path) -> PreparedRecording:
        del config, workspace
        if entry.source_entry_index == 2:
            self.third_prepared.set()
        digest = sha256_file(entry.audio_resolved_path)
        return PreparedRecording(
            recording_id=entry.id,
            source_audio_sha256=digest,
            source_codec="pcm_s16le",
            source_sample_rate_hz=16000,
            source_channels=1,
            source_duration_s=1.0,
            source_frame_count=16000,
            canonical_pcm_path=entry.audio_resolved_path,
            canonical_pcm_sha256=digest,
            canonical_sample_rate_hz=16000,
            canonical_channels=1,
            canonical_frame_count=16000,
            alignment_audio_path=entry.audio_resolved_path,
            alignment_audio_sha256=digest,
            alignment_sample_rate_hz=16000,
            alignment_channels=1,
            decoder_name="fixture",
            decoder_version="1",
            preparation_fingerprint="profile",
        )

    def status(self) -> dict[str, Any]:
        return {}


class BackgroundBatchStore:
    def __init__(self, third_prepared: Event) -> None:
        self.third_prepared = third_prepared
        self.batch_sizes: list[int] = []
        self.overlapped = False

    def ensure_many(
        self, requests: tuple[AudioUploadRequest, ...]
    ) -> tuple[RemoteAudioObject, ...]:
        self.batch_sizes.append(len(requests))
        if len(self.batch_sizes) == 1:
            self.overlapped = self.third_prepared.wait(timeout=2)
        return tuple(
            RemoteAudioObject(storage_key_for_sha256(request.sha256), uploaded=True)
            for request in requests
        )

    def ensure(self, source: Path, sha256: str) -> RemoteAudioObject:
        del source
        return RemoteAudioObject(storage_key_for_sha256(sha256), uploaded=True)

    def fetch(self, storage_key: str, sha256: str, destination: Path) -> Path:
        del storage_key, sha256
        return destination

    def status(self) -> dict[str, Any]:
        return {}


def test_upload_batches_run_while_later_audio_is_prepared(tmp_path: Path) -> None:
    third_prepared = Event()
    remote = BackgroundBatchStore(third_prepared)
    service = AlignmentService(
        AppConfig(remote_audio=RemoteAudioConfig(batch_size=2, upload_workers=1)),
        artifact_store=PreparingStore(third_prepared),
        audio_object_store=remote,
    )
    entries = tuple(_entry(tmp_path, index) for index in range(3))

    outcomes = service._prepare_and_upload(entries, tmp_path / "workspace")

    assert set(outcomes) == {entry.id for entry in entries}
    assert remote.batch_sizes == [2, 1]
    assert remote.overlapped is True
