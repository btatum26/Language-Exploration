from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    text: str = ""
    language: str | None = None
    review_state: str | None = None
    speaker_id: str | None = None
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class RecordingSummary:
    recording_id: str
    recording_version_id: str
    speaker_id: str | None
    transcript: str
    language: str
    recording_created_at: datetime | None
    version_created_at: datetime | None
    alignment_state: str
    review_state: str
    audio_available: bool
    audio_relative_path: str | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class CatalogPage:
    items: tuple[RecordingSummary, ...]
    offset: int
    total: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class SegmentData:
    id: str
    kind: str
    label: str
    start_sample: int
    end_sample: int
    timebase_sample_rate_hz: int
    parent_id: str | None = None
    confidence: float | None = None
    review_state: str = "automatic"
    model_label: str | None = None
    model_start_sample: int | None = None
    model_end_sample: int | None = None
    effective_revision_id: str | None = None
    model_segment_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecordingDetail:
    summary: RecordingSummary
    audio_path: Path | None
    words: tuple[SegmentData, ...]
    phones: tuple[SegmentData, ...]
    versions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    segment_id: str
    base_run_id: str
    start_sample: int | None
    end_sample: int | None
    label: str | None
    review_state: str
    author: str | None = None
    reason: str | None = None
    operation: str = "update"
    affected_segment_ids: tuple[str, ...] = ()
    replacement_segments: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class IngestRequest:
    recording_id: str
    audio_path: Path
    transcript: str
    language: str
    speaker_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    overwrite_existing: bool = False
