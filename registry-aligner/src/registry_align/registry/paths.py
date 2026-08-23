"""Cross-platform portable-path validation and containment."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class AudioPathError(ValueError):
    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


@dataclass(frozen=True)
class ResolvedAudioPath:
    portable_path: str
    normalized_key: str
    resolved_path: Path


def resolve_audio_path(registry_root: Path, raw_path: str) -> ResolvedAudioPath:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AudioPathError("AUDIO_PATH_REQUIRED", "audio path must be a non-empty string")

    portable = raw_path.replace("\\", "/")
    posix = PurePosixPath(portable)
    windows = PureWindowsPath(raw_path)
    if posix.is_absolute() or windows.is_absolute() or bool(windows.drive):
        raise AudioPathError(
            "AUDIO_PATH_ABSOLUTE",
            f"absolute audio path is not allowed: {raw_path!r}",
            "Use a path relative to the registry file.",
        )
    if ".." in posix.parts:
        raise AudioPathError(
            "AUDIO_PATH_TRAVERSAL",
            f"parent traversal is not allowed in audio path: {raw_path!r}",
            "Keep audio within the extracted registry directory.",
        )

    normalized_parts = tuple(part for part in posix.parts if part not in ("", "."))
    if not normalized_parts:
        raise AudioPathError("AUDIO_PATH_REQUIRED", "audio path must name a file")
    portable_normalized = PurePosixPath(*normalized_parts).as_posix()
    registry_resolved = registry_root.resolve(strict=True)
    candidate = registry_resolved.joinpath(*normalized_parts)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AudioPathError(
            "AUDIO_FILE_MISSING",
            f"audio file does not exist: {portable_normalized}",
            "Restore the file at the registry-relative path; files are not searched elsewhere.",
        ) from exc
    except OSError as exc:
        raise AudioPathError("AUDIO_PATH_UNREADABLE", f"cannot resolve audio path: {exc}") from exc

    try:
        resolved.relative_to(registry_resolved)
    except ValueError as exc:
        raise AudioPathError(
            "AUDIO_PATH_ESCAPE",
            f"audio path resolves outside the registry directory: {portable_normalized}",
            "Replace external symlinks with files contained by the registry directory.",
        ) from exc
    if not resolved.is_file():
        raise AudioPathError(
            "AUDIO_PATH_NOT_FILE", f"audio path is not a regular file: {portable_normalized}"
        )
    if not os.access(resolved, os.R_OK):
        raise AudioPathError(
            "AUDIO_FILE_UNREADABLE", f"audio file is not readable: {portable_normalized}"
        )

    normalized_key = unicodedata.normalize("NFC", portable_normalized).casefold()
    return ResolvedAudioPath(
        portable_path=portable_normalized,
        normalized_key=normalized_key,
        resolved_path=resolved,
    )
