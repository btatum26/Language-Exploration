"""Immutable local WAV storage behind the application audio port."""

from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit
from uuid import UUID

from application.contracts import AudioVerificationResult, ResolvedAudio
from application.errors import (
    AudioIntegrityError,
    AudioUnavailableError,
    UnsupportedAudioError,
)
from models import AudioAsset

_CHUNK_SIZE = 1024 * 1024
_URI_SCHEME = "registry-audio"
_URI_AUTHORITY = "assets"


class _WavMetadata(NamedTuple):
    codec: str
    sample_rate_hz: int
    frame_count: int
    channels: int


class LocalAudioStorage:
    """Store immutable WAV files below one configured root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._assets_root = self._root / "assets"
        try:
            self._assets_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AudioUnavailableError(
                f"could not initialize audio storage at {self._root}"
            ) from exc

    @property
    def root(self) -> Path:
        return self._root

    def ingest(self, source_path: Path, *, asset_id: UUID) -> AudioAsset:
        try:
            source = source_path.resolve(strict=True)
        except OSError as exc:
            raise AudioUnavailableError(f"audio source {source_path} is unavailable") from exc
        if not source.is_file():
            raise AudioUnavailableError(f"audio source {source} is not a regular file")
        if source.suffix.lower() != ".wav":
            raise UnsupportedAudioError("local audio storage currently accepts WAV files only")

        temporary_path: Path | None = None
        digest = hashlib.sha256()
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{asset_id}-",
                suffix=".tmp",
                dir=self._assets_root,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as destination, source.open("rb") as source_file:
                while chunk := source_file.read(_CHUNK_SIZE):
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())

            metadata = self._read_wav_metadata(temporary_path)
            final_path = self._assets_root / f"{asset_id}.wav"
            self._publish(temporary_path, final_path, digest.hexdigest())
            temporary_path = None
        except (AudioIntegrityError, UnsupportedAudioError):
            raise
        except OSError as exc:
            raise AudioUnavailableError(f"could not ingest audio source {source}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return AudioAsset(
            id=asset_id,
            sha256=digest.hexdigest(),
            storage_uri=f"{_URI_SCHEME}://{_URI_AUTHORITY}/{asset_id}",
            logical_path=source.name,
            media_type="audio/wav",
            original_extension=".wav",
            codec=metadata.codec,
            sample_rate_hz=metadata.sample_rate_hz,
            frame_count=metadata.frame_count,
            channels=metadata.channels,
            source_metadata={"original_name": source.name},
        )

    def resolve(self, audio_asset: AudioAsset) -> ResolvedAudio:
        target = self._target_for_asset(audio_asset)
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise AudioUnavailableError(
                f"audio asset {audio_asset.id} is not available below the configured root"
            ) from exc
        if not resolved.is_relative_to(self._assets_root) or not resolved.is_file():
            raise AudioUnavailableError(
                f"audio asset {audio_asset.id} does not resolve to a regular stored file"
            )
        return ResolvedAudio(asset=audio_asset, local_path=resolved)

    def verify(self, audio_asset: AudioAsset) -> AudioVerificationResult:
        target = self._target_for_asset(audio_asset)
        if not target.is_file():
            return AudioVerificationResult(
                asset=audio_asset,
                local_path=None,
                exists=False,
                hash_matches=False,
            )
        actual_sha256 = self._sha256(target)
        return AudioVerificationResult(
            asset=audio_asset,
            local_path=target.resolve(),
            exists=True,
            hash_matches=actual_sha256 == audio_asset.sha256,
            actual_sha256=actual_sha256,
        )

    def _target_for_asset(self, audio_asset: AudioAsset) -> Path:
        parsed = urlsplit(audio_asset.storage_uri)
        expected_path = f"/{audio_asset.id}"
        if (
            parsed.scheme != _URI_SCHEME
            or parsed.netloc != _URI_AUTHORITY
            or parsed.path != expected_path
            or parsed.query
            or parsed.fragment
        ):
            raise AudioIntegrityError(
                "audio storage URI must use registry-audio://assets/<audio-asset-uuid>"
            )
        try:
            uri_asset_id = UUID(parsed.path.removeprefix("/"))
        except ValueError as exc:
            raise AudioIntegrityError("audio storage URI contains an invalid asset UUID") from exc
        if uri_asset_id != audio_asset.id:
            raise AudioIntegrityError("audio storage URI does not match the audio asset ID")
        extension = (audio_asset.original_extension or ".wav").lower()
        if extension != ".wav":
            raise UnsupportedAudioError("local audio storage currently resolves WAV files only")
        return self._assets_root / f"{audio_asset.id}{extension}"

    @staticmethod
    def _read_wav_metadata(path: Path) -> _WavMetadata:
        try:
            with wave.open(str(path), "rb") as source:
                if source.getcomptype() != "NONE":
                    raise UnsupportedAudioError("compressed WAV audio is not supported")
                sample_width = source.getsampwidth()
                codecs = {
                    1: "pcm_u8",
                    2: "pcm_s16le",
                    3: "pcm_s24le",
                    4: "pcm_s32le",
                }
                try:
                    codec = codecs[sample_width]
                except KeyError as exc:
                    raise UnsupportedAudioError(
                        f"unsupported PCM sample width: {sample_width} bytes"
                    ) from exc
                return _WavMetadata(
                    codec=codec,
                    sample_rate_hz=source.getframerate(),
                    frame_count=source.getnframes(),
                    channels=source.getnchannels(),
                )
        except (EOFError, wave.Error) as exc:
            raise UnsupportedAudioError("audio source is not a readable WAV file") from exc

    def _publish(self, temporary_path: Path, final_path: Path, expected_sha256: str) -> None:
        try:
            os.link(temporary_path, final_path)
        except FileExistsError:
            if self._sha256(final_path) != expected_sha256:
                raise AudioIntegrityError(
                    f"stored audio target for asset {final_path.stem} contains different bytes"
                ) from None
        temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                while chunk := source.read(_CHUNK_SIZE):
                    digest.update(chunk)
        except OSError as exc:
            raise AudioUnavailableError(f"could not read stored audio {path}") from exc
        return digest.hexdigest()
