"""Durable filesystem recovery outbox for deterministic recording operations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from application.contracts import RecoveryEnvelope
from application.errors import InvalidRecoveryEnvelopeError, RecoveryStorageError


class FileRecoveryOutbox:
    """Persist pending operations atomically and retain every terminal record."""

    _DIRECTORIES = ("pending", "conflicts", "applied", "archive", "quarantine")

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        try:
            for name in self._DIRECTORIES:
                (self._root / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RecoveryStorageError(
                f"could not initialize recovery outbox at {self._root}"
            ) from exc

    @property
    def root(self) -> Path:
        return self._root

    def enqueue(self, envelope: RecoveryEnvelope) -> None:
        existing = self._find_any(envelope.operation_id)
        if existing is not None:
            existing_envelope = self._read(existing, quarantine=False)
            if existing_envelope is None:
                raise InvalidRecoveryEnvelopeError(
                    f"recovery operation {envelope.operation_id} could not be read"
                )
            if (
                existing_envelope.operation_kind != envelope.operation_kind
                or existing_envelope.request != envelope.request
            ):
                raise InvalidRecoveryEnvelopeError(
                    f"recovery operation {envelope.operation_id} already has different content"
                )
            return
        self._atomic_write(self._path("pending", envelope.operation_id), envelope)

    def list_pending(self) -> tuple[RecoveryEnvelope, ...]:
        return self._list_directory("pending")

    def list_conflicts(self) -> tuple[RecoveryEnvelope, ...]:
        return self._list_directory("conflicts")

    def get_active(self, operation_id: UUID) -> RecoveryEnvelope | None:
        for directory in ("pending", "conflicts"):
            path = self._path(directory, operation_id)
            if path.is_file():
                return self._read(path, quarantine=True)
        return None

    def mark_applied(self, operation_id: UUID) -> None:
        source = self._find_in(("pending", "conflicts"), operation_id)
        if source is None:
            if self._path("applied", operation_id).is_file():
                return
            raise InvalidRecoveryEnvelopeError(
                f"active recovery operation {operation_id} was not found"
            )
        self._move(source, self._path("applied", operation_id))

    def mark_conflict(
        self,
        operation_id: UUID,
        *,
        current_head_revision_id: UUID | None,
    ) -> None:
        source = self._find_in(("pending", "conflicts"), operation_id)
        if source is None:
            raise InvalidRecoveryEnvelopeError(
                f"active recovery operation {operation_id} was not found"
            )
        envelope = self._read(source, quarantine=False)
        if envelope is None:
            raise InvalidRecoveryEnvelopeError(
                f"recovery operation {operation_id} could not be read"
            )
        envelope = envelope.model_copy(
            update={"current_head_revision_id": current_head_revision_id}
        )
        destination = self._path("conflicts", operation_id)
        self._atomic_write(destination, envelope)
        if source != destination:
            try:
                source.unlink()
            except OSError as exc:
                raise RecoveryStorageError(
                    f"could not finish conflict transition for {operation_id}"
                ) from exc

    def archive(self, operation_id: UUID) -> None:
        source = self._find_in(("pending", "conflicts"), operation_id)
        if source is None:
            if self._path("archive", operation_id).is_file():
                return
            raise InvalidRecoveryEnvelopeError(
                f"active recovery operation {operation_id} was not found"
            )
        self._move(source, self._path("archive", operation_id))

    def _list_directory(self, directory: str) -> tuple[RecoveryEnvelope, ...]:
        envelopes: list[RecoveryEnvelope] = []
        try:
            paths = tuple(sorted((self._root / directory).glob("*.json")))
        except OSError as exc:
            raise RecoveryStorageError(f"could not scan recovery {directory}") from exc
        for path in paths:
            envelope = self._read(path, quarantine=True)
            if envelope is not None:
                envelopes.append(envelope)
        return tuple(
            sorted(
                envelopes,
                key=lambda item: (item.created_at, str(item.operation_id)),
            )
        )

    def _read(
        self,
        path: Path,
        *,
        quarantine: bool,
    ) -> RecoveryEnvelope | None:
        try:
            envelope = RecoveryEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
            if path.stem != str(envelope.operation_id):
                raise ValueError("filename does not match operation_id")
            return envelope
        except OSError as exc:
            raise RecoveryStorageError(f"could not read recovery envelope {path}") from exc
        except (TypeError, ValueError) as exc:
            if quarantine:
                self._quarantine(path)
                return None
            raise InvalidRecoveryEnvelopeError(f"recovery envelope {path.name} is invalid") from exc

    def _quarantine(self, path: Path) -> None:
        destination = self._root / "quarantine" / f"{path.stem}-{uuid4()}.json"
        self._move(path, destination)

    def _atomic_write(self, destination: Path, envelope: RecoveryEnvelope) -> None:
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{envelope.operation_id}-",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(envelope.to_deterministic_json())
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as exc:
            raise RecoveryStorageError(
                f"could not persist recovery operation {envelope.operation_id}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _move(source: Path, destination: Path) -> None:
        try:
            os.replace(source, destination)
        except OSError as exc:
            raise RecoveryStorageError(
                f"could not move recovery envelope {source.name} to {destination.parent.name}"
            ) from exc

    def _path(self, directory: str, operation_id: UUID) -> Path:
        return self._root / directory / f"{operation_id}.json"

    def _find_in(self, directories: tuple[str, ...], operation_id: UUID) -> Path | None:
        for directory in directories:
            path = self._path(directory, operation_id)
            if path.is_file():
                return path
        return None

    def _find_any(self, operation_id: UUID) -> Path | None:
        return self._find_in(
            ("pending", "conflicts", "applied", "archive"),
            operation_id,
        )
