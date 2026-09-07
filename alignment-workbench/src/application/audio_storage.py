"""Immutable local WAV and MP3 storage behind the application audio port."""

from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit
from uuid import UUID

import soundfile as sf  # type: ignore[import-untyped]

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


class _AudioMetadata(NamedTuple):
    codec: str
    sample_rate_hz: int
    frame_count: int
    channels: int


class _AudioFormat(NamedTuple):
    media_type: str
    extension: str


_AUDIO_FORMATS = {
    ".wav": _AudioFormat(media_type="audio/wav", extension=".wav"),
    ".mp3": _AudioFormat(media_type="audio/mpeg", extension=".mp3"),
}


class LocalAudioStorage:
    """Store immutable PCM WAV and MP3 files below one configured root."""

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
        suffix = source.suffix.lower()
        try:
            audio_format = _AUDIO_FORMATS[suffix]
        except KeyError as exc:
            raise UnsupportedAudioError(
                "local audio storage accepts uncompressed PCM WAV and MP3 files"
            ) from exc

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

            metadata = self._read_metadata(temporary_path, suffix)
            final_path = self._assets_root / f"{asset_id}{audio_format.extension}"
            self._publish(temporary_path, final_path, digest.hexdigest())
            temporary_path = None
        except (AudioIntegrityError, UnsupportedAudioError):
            raise
        except OSError as exc:
            raise AudioUnavailableError(f"could not ingest audio source {source}") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return AudioAsset(
            id=asset_id,
            sha256=digest.hexdigest(),
            storage_uri=f"{_URI_SCHEME}://{_URI_AUTHORITY}/{asset_id}",
            logical_path=source.name,
            media_type=audio_format.media_type,
            original_extension=audio_format.extension,
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
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            return AudioVerificationResult(
                asset=audio_asset,
                local_path=None,
                exists=False,
                hash_matches=False,
            )
        except OSError as exc:
            raise AudioUnavailableError(f"could not resolve stored audio {audio_asset.id}") from exc
        if not resolved.is_relative_to(self._assets_root) or not resolved.is_file():
            raise AudioIntegrityError(
                f"audio asset {audio_asset.id} resolves outside the configured storage root"
            )
        actual_sha256 = self._sha256(resolved)
        return AudioVerificationResult(
            asset=audio_asset,
            local_path=resolved,
            exists=True,
            hash_matches=actual_sha256 == audio_asset.sha256,
            actual_sha256=actual_sha256,
        )

    def discard(self, audio_asset: AudioAsset) -> None:
        """Remove a failed import only when its bytes still match the staged asset."""

        target = self._target_for_asset(audio_asset)
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AudioUnavailableError(f"could not resolve stored audio {audio_asset.id}") from exc
        if not resolved.is_relative_to(self._assets_root) or not resolved.is_file():
            raise AudioIntegrityError(
                f"refusing to discard audio asset {audio_asset.id} outside the storage root"
            )
        if self._sha256(resolved) != audio_asset.sha256:
            raise AudioIntegrityError(
                f"refusing to discard audio asset {audio_asset.id} with changed bytes"
            )
        try:
            resolved.unlink()
        except OSError as exc:
            raise AudioUnavailableError(f"could not discard audio asset {audio_asset.id}") from exc

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
        if extension not in _AUDIO_FORMATS:
            raise UnsupportedAudioError(
                "local audio storage resolves uncompressed PCM WAV and MP3 files"
            )
        return self._assets_root / f"{audio_asset.id}{extension}"

    @classmethod
    def _read_metadata(cls, path: Path, extension: str) -> _AudioMetadata:
        if extension == ".wav":
            return cls._read_wav_metadata(path)
        return cls._read_mp3_metadata(path)

    @staticmethod
    def _read_wav_metadata(path: Path) -> _AudioMetadata:
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
                return _AudioMetadata(
                    codec=codec,
                    sample_rate_hz=source.getframerate(),
                    frame_count=source.getnframes(),
                    channels=source.getnchannels(),
                )
        except (EOFError, wave.Error) as exc:
            raise UnsupportedAudioError("audio source is not a readable WAV file") from exc

    @staticmethod
    def _read_mp3_metadata(path: Path) -> _AudioMetadata:
        try:
            metadata = sf.info(path)
        except (OSError, RuntimeError) as exc:
            raise UnsupportedAudioError("audio source is not a readable MP3 file") from exc
        if metadata.format != "MP3" or metadata.subtype != "MPEG_LAYER_III":
            raise UnsupportedAudioError("audio source does not contain MPEG Layer III audio")
        if metadata.samplerate <= 0 or metadata.frames <= 0 or metadata.channels <= 0:
            raise UnsupportedAudioError("MP3 audio metadata must describe a nonempty signal")
        return _AudioMetadata(
            codec="mp3",
            sample_rate_hz=int(metadata.samplerate),
            frame_count=int(metadata.frames),
            channels=int(metadata.channels),
        )

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
