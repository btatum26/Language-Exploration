"""Models passed across the backend boundary."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.segments import AlignmentSegment
from registry_align.domain.transcripts import NormalizedTranscript
from registry_align.events import Issue


class AlignmentJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: RegistryEntry
    prepared: PreparedRecording
    transcript: NormalizedTranscript


class BackendDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usable: bool
    name: str
    version: str | None = None
    capabilities: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()
    remediation_commands: tuple[str, ...] = ()


class BackendResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segments: dict[str, tuple[AlignmentSegment, ...]]
    issues: tuple[Issue, ...] = ()
    raw_output_directory: Path
    command: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
