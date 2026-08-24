from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from registry_align.config import RemoteAudioConfig
from registry_align.errors import ProcessingError
from registry_align.remote_audio import (
    AudioUploadRequest,
    SshAudioObjectStore,
    storage_key_for_sha256,
)


def test_storage_key_depends_only_on_source_hash() -> None:
    digest = hashlib.sha256(b"same audio").hexdigest()

    assert storage_key_for_sha256(digest) == f"{digest[:2]}/{digest}"


def test_storage_key_rejects_non_sha_values() -> None:
    with pytest.raises(ProcessingError, match="64 lowercase"):
        storage_key_for_sha256("../audio.mp3")


def test_ensure_reuses_verified_remote_object(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    digest = hashlib.sha256(b"audio").hexdigest()
    store = SshAudioObjectStore(RemoteAudioConfig())
    monkeypatch.setattr(store, "_remote_hashes", lambda keys: {keys[0]: digest})

    result = store.ensure(source, digest)

    assert result.storage_key == f"{digest[:2]}/{digest}"
    assert result.uploaded is False


def test_missing_objects_share_one_scp_batch(tmp_path: Path, monkeypatch: Any) -> None:
    requests: list[AudioUploadRequest] = []
    for index in range(3):
        source = tmp_path / f"source-{index}.mp3"
        source.write_bytes(f"audio-{index}".encode())
        requests.append(AudioUploadRequest(source, hashlib.sha256(source.read_bytes()).hexdigest()))
    store = SshAudioObjectStore(RemoteAudioConfig())
    commands: list[list[str]] = []
    finalized: list[tuple[str, tuple[AudioUploadRequest, ...]]] = []
    monkeypatch.setattr(
        store,
        "_remote_hashes",
        lambda keys: dict.fromkeys(keys),
    )
    monkeypatch.setattr(
        store,
        "_run",
        lambda command: commands.append(command),
    )
    monkeypatch.setattr(
        store,
        "_finalize_batch",
        lambda batch_id, missing: finalized.append((batch_id, missing)),
    )

    results = store.ensure_many(tuple(requests))

    assert len(commands) == 1
    assert commands[0][0] == "scp"
    assert len(commands[0]) == 5
    assert len(finalized) == 1
    assert finalized[0][1] == tuple(requests)
    assert all(result.uploaded for result in results)


def test_fetch_verifies_download_before_atomic_publish(tmp_path: Path, monkeypatch: Any) -> None:
    payload = b"audio"
    digest = hashlib.sha256(payload).hexdigest()
    key = storage_key_for_sha256(digest)
    destination = tmp_path / "recording.mp3"
    store = SshAudioObjectStore(RemoteAudioConfig())

    def run(command: list[str]) -> Any:
        Path(command[-1]).write_bytes(payload)
        return object()

    monkeypatch.setattr(store, "_run", run)

    assert store.fetch(key, digest, destination) == destination
    assert destination.read_bytes() == payload
