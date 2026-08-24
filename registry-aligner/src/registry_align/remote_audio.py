"""Batched content-addressed source audio stored through OpenSSH."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from registry_align.audio.probe import sha256_file
from registry_align.config import RemoteAudioConfig
from registry_align.errors import DependencyError, ProcessingError


@dataclass(frozen=True)
class AudioUploadRequest:
    source: Path
    sha256: str


@dataclass(frozen=True)
class RemoteAudioObject:
    storage_key: str
    uploaded: bool


class AudioObjectStore(Protocol):
    def ensure(self, source: Path, sha256: str) -> RemoteAudioObject: ...

    def ensure_many(
        self, requests: tuple[AudioUploadRequest, ...]
    ) -> tuple[RemoteAudioObject, ...]: ...

    def fetch(self, storage_key: str, sha256: str, destination: Path) -> Path: ...

    def status(self) -> dict[str, Any]: ...


def storage_key_for_sha256(sha256: str) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ProcessingError("audio SHA-256 must be 64 lowercase hexadecimal characters")
    return f"{sha256[:2]}/{sha256}"


class SshAudioObjectStore:
    """Stores one exact source object for each SHA-256 on the SSH server."""

    def __init__(self, config: RemoteAudioConfig) -> None:
        self.config = config
        self._creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.config.operation_timeout_seconds,
                creationflags=self._creation_flags,
            )
        except FileNotFoundError as exc:
            raise DependencyError(f"required executable is unavailable: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError(f"remote audio operation timed out: {command[0]}") from exc
        except OSError as exc:
            raise ProcessingError(f"remote audio operation failed to start: {exc}") from exc
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "unknown SSH error"
            raise ProcessingError(f"remote audio operation failed: {message}")
        return result

    def _remote_shell_path(self, storage_key: str = "") -> str:
        suffix = self.config.root[2:]
        components = [component for component in f"{suffix}/{storage_key}".split("/") if component]
        if any(component in {".", ".."} for component in components):
            raise ProcessingError("remote audio path contains an unsafe component")
        return '"$HOME"/' + "/".join(components)

    def _remote_scp_path(self, storage_key: str) -> str:
        return f"{self.config.ssh_host}:{self.config.root}/{storage_key}"

    def _ssh(self, command: str) -> subprocess.CompletedProcess[str]:
        return self._run([self.config.ssh_executable, self.config.ssh_host, command])

    @staticmethod
    def _parse_hash_lines(output: str) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            result[parts[0]] = None if parts[1] == "MISSING" else parts[1].lower()
        return result

    def _remote_hashes(self, storage_keys: tuple[str, ...]) -> dict[str, str | None]:
        if not storage_keys:
            return {}
        keys = " ".join(storage_keys)
        root = self._remote_shell_path()
        command = (
            f'set -e\nroot={root}\nmkdir -p "$root/.staging"\n'
            f"for key in {keys}; do\n"
            '  path="$root/$key"\n'
            '  if test -f "$path"; then\n'
            "    digest=$(sha256sum \"$path\" | cut -d ' ' -f 1)\n"
            '    printf \'%s %s\\n\' "$key" "$digest"\n'
            "  else\n"
            "    printf '%s MISSING\\n' \"$key\"\n"
            "  fi\n"
            "done"
        )
        hashes = self._parse_hash_lines(self._ssh(command).stdout)
        if set(hashes) != set(storage_keys):
            raise ProcessingError("remote audio batch preflight returned an incomplete result")
        return hashes

    def _finalize_batch(
        self,
        batch_id: str,
        missing: tuple[AudioUploadRequest, ...],
    ) -> None:
        root = self._remote_shell_path()
        commands = ["set -e", f"root={root}"]
        for request in missing:
            key = storage_key_for_sha256(request.sha256)
            temporary_name = f"{batch_id}-{request.sha256}"
            commands.extend(
                (
                    f'temporary="$root/.staging/{temporary_name}"',
                    f'destination="$root/{key}"',
                    "actual=$(sha256sum \"$temporary\" | cut -d ' ' -f 1)",
                    f'test "$actual" = "{request.sha256}"',
                    f'mkdir -p "$root/{request.sha256[:2]}"',
                    'if test -f "$destination"; then rm -f "$temporary"; '
                    'else mv "$temporary" "$destination"; fi',
                    "actual=$(sha256sum \"$destination\" | cut -d ' ' -f 1)",
                    f'test "$actual" = "{request.sha256}"',
                )
            )
        self._ssh("\n".join(commands))

    def _cleanup_staging(self, batch_id: str, requests: tuple[AudioUploadRequest, ...]) -> None:
        if not requests:
            return
        paths = " ".join(f'"$root/.staging/{batch_id}-{request.sha256}"' for request in requests)
        try:
            self._ssh(f"root={self._remote_shell_path()}\nrm -f {paths}")
        except (DependencyError, ProcessingError):
            pass

    def ensure(self, source: Path, sha256: str) -> RemoteAudioObject:
        return self.ensure_many((AudioUploadRequest(source, sha256),))[0]

    def ensure_many(
        self, requests: tuple[AudioUploadRequest, ...]
    ) -> tuple[RemoteAudioObject, ...]:
        if not requests:
            return ()
        unique: dict[str, AudioUploadRequest] = {}
        for request in requests:
            source = request.source.resolve(strict=True)
            storage_key_for_sha256(request.sha256)
            if sha256_file(source) != request.sha256:
                raise ProcessingError(f"source audio changed while uploading: {source}")
            unique.setdefault(request.sha256, AudioUploadRequest(source, request.sha256))

        keys = tuple(storage_key_for_sha256(digest) for digest in unique)
        existing = self._remote_hashes(keys)
        for digest, key in zip(unique, keys, strict=True):
            remote_hash = existing[key]
            if remote_hash not in {None, digest}:
                raise ProcessingError(
                    f"remote audio hash mismatch for immutable object {key}: {remote_hash}"
                )
        missing = tuple(
            request
            for digest, request in unique.items()
            if existing[storage_key_for_sha256(digest)] is None
        )
        if missing:
            batch_id = uuid.uuid4().hex
            try:
                with TemporaryDirectory(prefix="registry-align-upload-") as directory_name:
                    directory = Path(directory_name)
                    staged_paths: list[str] = []
                    for request in missing:
                        staged = directory / f"{batch_id}-{request.sha256}"
                        try:
                            os.link(request.source, staged)
                        except OSError:
                            shutil.copy2(request.source, staged)
                        staged_paths.append(str(staged))
                    self._run(
                        [
                            self.config.scp_executable,
                            *staged_paths,
                            self._remote_scp_path(".staging/"),
                        ]
                    )
                    self._finalize_batch(batch_id, missing)
            except Exception:
                self._cleanup_staging(batch_id, missing)
                raise
        uploaded = {request.sha256 for request in missing}
        return tuple(
            RemoteAudioObject(
                storage_key=storage_key_for_sha256(request.sha256),
                uploaded=request.sha256 in uploaded,
            )
            for request in requests
        )

    def fetch(self, storage_key: str, sha256: str, destination: Path) -> Path:
        if storage_key != storage_key_for_sha256(sha256):
            raise ProcessingError("database audio storage key does not match its SHA-256")
        if destination.exists():
            raise ProcessingError(f"audio destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._run(
                [self.config.scp_executable, self._remote_scp_path(storage_key), str(temporary)]
            )
            if sha256_file(temporary) != sha256:
                raise ProcessingError(f"downloaded audio hash verification failed: {storage_key}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def status(self) -> dict[str, Any]:
        for executable in (self.config.ssh_executable, self.config.scp_executable):
            if shutil.which(executable) is None and not Path(executable).is_file():
                raise DependencyError(f"required executable is unavailable: {executable}")
        root = self._remote_shell_path()
        result = self._ssh(
            f"mkdir -p {root}/.staging && test -w {root} && "
            f"find {root} -mindepth 2 -maxdepth 2 -type f -printf '%s\\n'"
        )
        sizes = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
        return {
            "usable": True,
            "host": self.config.ssh_host,
            "root": self.config.root,
            "objects": len(sizes),
            "bytes": sum(sizes),
            "batch_size": self.config.batch_size,
            "upload_workers": self.config.upload_workers,
        }
