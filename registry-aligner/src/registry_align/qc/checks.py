"""Non-prescriptive structural alignment checks."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from registry_align.domain.audio import PreparedRecording
from registry_align.domain.segments import AlignmentSegment
from registry_align.domain.transcripts import NormalizedTranscript
from registry_align.events import Issue, Severity


def check_alignment(
    run_id: str,
    prepared: PreparedRecording,
    transcript: NormalizedTranscript,
    segments: tuple[AlignmentSegment, ...],
    *,
    long_recording_threshold_s: float = 60.0,
) -> tuple[Issue, ...]:
    issues: list[Issue] = []

    def add(code: str, severity: Severity, message: str, **details: object) -> None:
        issues.append(
            Issue(
                code=code,
                severity=severity,
                stage="qc",
                recording_id=prepared.recording_id,
                run_id=run_id,
                message=message,
                details=details,
            )
        )

    by_tier: dict[str, list[AlignmentSegment]] = {}
    for segment in segments:
        by_tier.setdefault(segment.tier, []).append(segment)
        if segment.end_sample > prepared.canonical_frame_count:
            add(
                "SEGMENT_OUT_OF_BOUNDS",
                Severity.ERROR,
                "segment endpoint exceeds canonical PCM frame count",
                segment_id=segment.segment_id,
            )
    for tier, tier_segments in by_tier.items():
        ordered = sorted(tier_segments, key=lambda item: item.start_sample)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_sample < previous.end_sample:
                add(
                    "SEGMENT_OVERLAP",
                    Severity.ERROR,
                    f"ordered {tier} intervals overlap",
                    previous_segment_id=previous.segment_id,
                    segment_id=current.segment_id,
                )

    words = sorted(by_tier.get("word", []), key=lambda item: item.start_sample)
    phones = by_tier.get("phone", [])
    expected = [token.text.casefold() for token in transcript.alignment_tokens]
    actual = [segment.label.casefold() for segment in words]
    if expected != actual:
        add(
            "WORD_SEQUENCE_MISMATCH",
            Severity.WARNING,
            "aligned word sequence differs from the aligner transcript",
            expected=expected,
            actual=actual,
        )
    if not phones:
        add("EMPTY_PHONE_TIER", Severity.ERROR, "alignment contains no phone intervals")
    word_by_id = {word.segment_id: word for word in words}
    for phone in phones:
        parent = word_by_id.get(phone.parent_segment_id or "")
        if parent is not None and (
            phone.start_sample < parent.start_sample - 1 or phone.end_sample > parent.end_sample + 1
        ):
            add(
                "PHONE_OUTSIDE_WORD",
                Severity.WARNING,
                "phone interval falls outside its parent word",
                segment_id=phone.segment_id,
                parent_segment_id=parent.segment_id,
            )
    aligned_samples = sum(segment.end_sample - segment.start_sample for segment in words)
    coverage = (
        aligned_samples / prepared.canonical_frame_count if prepared.canonical_frame_count else 0.0
    )
    if coverage < 0.5:
        add(
            "LOW_ALIGNMENT_COVERAGE",
            Severity.WARNING,
            "aligned words cover less than half of the recording",
            coverage=coverage,
        )
    if prepared.source_duration_s > long_recording_threshold_s:
        add(
            "LONG_RECORDING_REVIEW",
            Severity.INFO,
            "long recording should receive manual boundary review",
            duration_s=prepared.source_duration_s,
            threshold_s=long_recording_threshold_s,
        )
    for warning in transcript.warnings:
        add("TRANSCRIPT_NORMALIZATION_WARNING", Severity.WARNING, warning)
    return tuple(issues)


def check_stored_alignments(
    run_id: str,
    recordings: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rerun storage-level invariant checks without an alignment backend."""
    by_recording: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        by_recording[str(segment["recording_id"])].append(segment)
    issues: list[dict[str, Any]] = []

    def add(recording_id: str, code: str, severity: str, message: str, **details: Any) -> None:
        issues.append(
            {
                "run_id": run_id,
                "recording_id": recording_id,
                "code": code,
                "severity": severity,
                "stage": "qc",
                "message": message,
                "hint": None,
                "details_json": json.dumps(details, ensure_ascii=False, sort_keys=True),
            }
        )

    for recording in recordings:
        recording_id = str(recording["recording_id"])
        recording_segments = by_recording.get(recording_id, [])
        if not recording_segments:
            add(recording_id, "ALIGNMENT_MISSING", "error", "recording has no effective segments")
            continue
        frame_count = int(recording.get("canonical_frame_count") or 0)
        for segment in recording_segments:
            if int(segment["start_sample"]) < 0 or int(segment["end_sample"]) <= int(
                segment["start_sample"]
            ):
                add(
                    recording_id,
                    "SEGMENT_INVALID_INTERVAL",
                    "error",
                    "segment has a non-positive interval",
                    segment_id=segment["segment_id"],
                )
            if frame_count and int(segment["end_sample"]) > frame_count:
                add(
                    recording_id,
                    "SEGMENT_OUT_OF_BOUNDS",
                    "error",
                    "segment endpoint exceeds canonical PCM frame count",
                    segment_id=segment["segment_id"],
                )
        for tier in ("word", "phone", "silence"):
            tier_segments = sorted(
                (item for item in recording_segments if item["tier"] == tier),
                key=lambda item: int(item["start_sample"]),
            )
            for previous, current in zip(tier_segments, tier_segments[1:], strict=False):
                if int(current["start_sample"]) < int(previous["end_sample"]):
                    add(
                        recording_id,
                        "SEGMENT_OVERLAP",
                        "error",
                        f"ordered {tier} intervals overlap",
                        segment_id=current["segment_id"],
                    )
        if not any(item["tier"] == "phone" for item in recording_segments):
            add(recording_id, "EMPTY_PHONE_TIER", "error", "alignment contains no phone intervals")
    return issues
