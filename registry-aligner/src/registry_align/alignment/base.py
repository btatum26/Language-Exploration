"""Backend protocol used by pipeline services."""

from pathlib import Path
from typing import Protocol

from registry_align.alignment.models import AlignmentJob, BackendDiagnostics, BackendResult


class AlignerBackend(Protocol):
    @property
    def name(self) -> str: ...

    def fingerprint(self) -> str: ...

    def doctor(self) -> BackendDiagnostics: ...

    def align(
        self, jobs: tuple[AlignmentJob, ...], workspace: Path, run_id: str
    ) -> BackendResult: ...
