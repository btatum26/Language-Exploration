"""Shared workbench commands, results, queries, and infrastructure protocols."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypeAlias
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    ConceptRef,
    CreateRecordingRequest,
    DomainModel,
    GeometryType,
    NonEmptyStr,
    PinnedLibraryVersion,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
)


@dataclass(frozen=True, slots=True)
class ResolvedAudio:
    asset: AudioAsset
    local_path: Path


@dataclass(frozen=True, slots=True)
class AudioVerificationResult:
    asset: AudioAsset
    local_path: Path | None
    exists: bool
    hash_matches: bool
    actual_sha256: str | None = None


class SyncState(StrEnum):
    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Saved:
    snapshot: AnnotatedRecordingSnapshot


@dataclass(frozen=True, slots=True)
class Queued:
    operation_id: UUID
    revision_id: UUID


@dataclass(frozen=True, slots=True)
class SaveConflict:
    operation_id: UUID
    expected_head_revision_id: UUID | None
    current_head_revision_id: UUID | None


SaveResult: TypeAlias = Saved | Queued | SaveConflict


class CreateRecordingCommand(DomainModel):
    recording_id: UUID
    initial_revision_id: UUID = Field(default_factory=uuid4)
    source_audio_path: Path
    name: NonEmptyStr
    language: NonEmptyStr
    default_speaker_ref: UUID | None = None
    libraries: tuple[PinnedLibraryVersion, ...] = ()
    annotations: tuple[SignalAnnotation, ...] = ()
    author: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class AnnotationQuery:
    start_sample: int | None = None
    end_sample: int | None = None
    min_frequency_hz: float | None = None
    max_frequency_hz: float | None = None
    concept_refs: frozenset[ConceptRef] = frozenset()
    namespaces: frozenset[str] = frozenset()
    geometry_types: frozenset[GeometryType] = frozenset()

    def __post_init__(self) -> None:
        if self.start_sample is not None and self.start_sample < 0:
            raise ValueError("start_sample must be nonnegative")
        if self.end_sample is not None and self.end_sample < 0:
            raise ValueError("end_sample must be nonnegative")
        if (
            self.start_sample is not None
            and self.end_sample is not None
            and self.end_sample <= self.start_sample
        ):
            raise ValueError("end_sample must be greater than start_sample")
        if self.min_frequency_hz is not None and self.min_frequency_hz < 0:
            raise ValueError("min_frequency_hz must be nonnegative")
        if self.max_frequency_hz is not None and self.max_frequency_hz < 0:
            raise ValueError("max_frequency_hz must be nonnegative")
        if (
            self.min_frequency_hz is not None
            and self.max_frequency_hz is not None
            and self.max_frequency_hz <= self.min_frequency_hz
        ):
            raise ValueError("max_frequency_hz must be greater than min_frequency_hz")


RecoveryOperationKind: TypeAlias = Literal["create_recording", "save_recording"]


class RecoveryEnvelope(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: UUID
    operation_kind: RecoveryOperationKind
    created_at: datetime
    request: CreateRecordingRequest | SaveRecordingSnapshotRequest
    current_head_revision_id: UUID | None = None

    @model_validator(mode="after")
    def request_matches_operation(self) -> RecoveryEnvelope:
        if self.operation_kind == "create_recording" and not isinstance(
            self.request, CreateRecordingRequest
        ):
            raise ValueError("create_recording requires CreateRecordingRequest")
        if self.operation_kind == "save_recording" and not isinstance(
            self.request, SaveRecordingSnapshotRequest
        ):
            raise ValueError("save_recording requires SaveRecordingSnapshotRequest")
        return self

    @property
    def recording_id(self) -> UUID:
        return self.request.recording_id

    @property
    def revision_id(self) -> UUID:
        if isinstance(self.request, CreateRecordingRequest):
            return self.request.initial_revision_id
        return self.request.new_revision_id

    @property
    def expected_head_revision_id(self) -> UUID | None:
        if isinstance(self.request, CreateRecordingRequest):
            return None
        return self.request.expected_parent_revision_id


@dataclass(frozen=True, slots=True)
class PendingSave:
    operation_id: UUID
    operation_kind: RecoveryOperationKind
    recording_id: UUID
    revision_id: UUID
    expected_head_revision_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryConflict:
    operation_id: UUID
    recording_id: UUID
    revision_id: UUID
    expected_head_revision_id: UUID | None
    current_head_revision_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryApplied:
    operation_id: UUID
    recording_id: UUID
    revision_id: UUID
    snapshot: AnnotatedRecordingSnapshot


RecoveryResult: TypeAlias = RecoveryApplied | PendingSave | RecoveryConflict


class AudioStorageHandler(Protocol):
    def ingest(self, source_path: Path, *, asset_id: UUID) -> AudioAsset: ...

    def resolve(self, audio_asset: AudioAsset) -> ResolvedAudio: ...

    def verify(self, audio_asset: AudioAsset) -> AudioVerificationResult: ...


class RecoveryOutbox(Protocol):
    def enqueue(self, envelope: RecoveryEnvelope) -> None: ...

    def list_pending(self) -> tuple[RecoveryEnvelope, ...]: ...

    def list_conflicts(self) -> tuple[RecoveryEnvelope, ...]: ...

    def get_active(self, operation_id: UUID) -> RecoveryEnvelope | None: ...

    def mark_applied(self, operation_id: UUID) -> None: ...

    def mark_conflict(
        self,
        operation_id: UUID,
        *,
        current_head_revision_id: UUID | None,
    ) -> None: ...

    def archive(self, operation_id: UUID) -> None: ...
