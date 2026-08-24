"""Persistence contracts and domain-facing storage results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.segments import AlignmentSegment, SegmentRevision
from registry_align.domain.transcripts import NormalizedTranscript
from registry_align.events import Issue


@dataclass(frozen=True)
class VersionIngestion:
    recording_version_id: UUID
    state: str


@dataclass(frozen=True)
class AlignmentClaim:
    alignment_result_id: UUID
    state: str


class MetadataRepository(Protocol):
    def existing_recording_ids(self, recording_ids: tuple[str, ...]) -> set[str]: ...

    def start_run(
        self,
        configuration_fingerprint: str,
        backend_summary: dict[str, Any],
        invoking_host: str | None,
    ) -> UUID: ...

    def finish_run(
        self,
        run_id: UUID,
        status: str,
        counts: dict[str, int],
        errors: list[dict[str, Any]],
    ) -> None: ...

    def save_run_item(
        self,
        run_id: UUID,
        recording_id: str,
        outcome: str,
        *,
        version_id: UUID | None = None,
        alignment_id: UUID | None = None,
        detail: str | None = None,
    ) -> None: ...

    def save_issue(self, run_id: UUID, issue: Issue) -> None: ...

    def ingest_version(
        self,
        entry: RegistryEntry,
        prepared: PreparedRecording,
        transcript: NormalizedTranscript,
        *,
        remote_storage_key: str,
        corpus_id: str,
        content_fingerprint: str,
        ingestion_reason: str,
        overwrite_existing: bool,
    ) -> VersionIngestion: ...

    def claim_alignment(
        self,
        version_id: UUID,
        processing_fingerprint: str,
        provenance: dict[str, str],
        *,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AlignmentClaim: ...

    def complete_alignment(
        self,
        alignment_id: UUID,
        segments: tuple[AlignmentSegment, ...],
        qc_summary: dict[str, Any],
    ) -> None: ...

    def fail_alignment(self, alignment_id: UUID, summary: dict[str, Any]) -> None: ...

    def latest_status(self) -> dict[str, Any] | None: ...

    def recording_history(self, recording_id: str) -> list[dict[str, Any]]: ...

    def catalog_recordings(
        self,
        *,
        text: str,
        language: str | None,
        review_state: str | None,
        speaker_id: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]: ...

    def recording_detail(self, recording_id: str) -> dict[str, Any] | None: ...

    def speakers(self, text: str = "") -> list[dict[str, Any]]: ...

    def create_speaker(
        self, speaker_id: str, display_name: str, language: str | None
    ) -> dict[str, Any]: ...

    def revision_history(self, segment_id: str) -> list[dict[str, Any]]: ...

    def segments_by_ids(self, segment_ids: tuple[str, ...]) -> list[dict[str, Any]]: ...

    def current_audio_asset(self, recording_id: str) -> dict[str, Any] | None: ...

    def effective_recordings(self) -> list[dict[str, Any]]: ...

    def effective_segments(self, recording_id: str | None = None) -> list[dict[str, Any]]: ...

    def save_revision(self, revision: SegmentRevision) -> UUID: ...

    def prune_runs(
        self,
        *,
        now: datetime,
        run_metadata_days: int,
        failed_run_days: int,
        minimum_runs_to_keep: int,
        dry_run: bool,
    ) -> list[UUID]: ...


class UnitOfWork(Protocol):
    repository: MetadataRepository

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(self, *args: object) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
