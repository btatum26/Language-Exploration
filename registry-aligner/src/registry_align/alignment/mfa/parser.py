"""MFA/PraatIO JSON tier conversion to canonical segments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from registry_align.alignment.mfa.staging import StagingEntry
from registry_align.alignment.models import AlignmentJob
from registry_align.audio.timebase import seconds_to_sample
from registry_align.domain.segments import AlignmentSegment
from registry_align.errors import ProcessingError
from registry_align.text.reconciliation import source_token_ids_for_word


def _tier_entries(payload: dict[str, Any]) -> dict[str, list[list[Any]]]:
    tiers = payload.get("tiers")
    if isinstance(tiers, dict):
        return {
            str(name): list(value.get("entries", []))
            for name, value in tiers.items()
            if isinstance(value, dict)
        }
    if isinstance(tiers, list):
        return {
            str(value.get("name", "")): list(value.get("entries", []))
            for value in tiers
            if isinstance(value, dict)
        }
    raise ProcessingError("MFA JSON output has no supported tiers object")


def _find_output(output_directory: Path, item: StagingEntry) -> Path:
    relative = Path(*item.relative_stem.split("/"))
    direct = output_directory / relative.with_suffix(".json")
    if direct.exists():
        return direct
    matches = list(output_directory.rglob(f"{relative.name}.json"))
    if len(matches) != 1:
        raise ProcessingError(f"MFA output missing or ambiguous for {item.recording_id}")
    return matches[0]


def _canonical_bounds(start_s: float, end_s: float, job: AlignmentJob) -> tuple[int, int]:
    start = seconds_to_sample(start_s, job.prepared.canonical_sample_rate_hz)
    end = seconds_to_sample(end_s, job.prepared.canonical_sample_rate_hz)
    if start < 0 or end <= start:
        raise ProcessingError(f"invalid MFA interval for {job.entry.id}: {start_s}..{end_s}")
    if end > job.prepared.canonical_frame_count:
        if end - job.prepared.canonical_frame_count <= 1:
            end = job.prepared.canonical_frame_count
        else:
            raise ProcessingError(f"MFA interval exceeds audio duration for {job.entry.id}")
    return start, end


def parse_results(
    output_directory: Path,
    manifest: tuple[StagingEntry, ...],
    jobs: tuple[AlignmentJob, ...],
    run_id: str,
    model_id: str,
) -> dict[str, tuple[AlignmentSegment, ...]]:
    jobs_by_id = {job.entry.id: job for job in jobs}
    result: dict[str, tuple[AlignmentSegment, ...]] = {}
    for manifest_item in manifest:
        job = jobs_by_id[manifest_item.recording_id]
        path = _find_output(output_directory, manifest_item)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            tiers = _tier_entries(payload)
        except (OSError, json.JSONDecodeError) as exc:
            raise ProcessingError(f"cannot parse MFA output {path}: {exc}") from exc
        word_entries: list[list[Any]] = []
        phone_entries: list[list[Any]] = []
        for name, entries in tiers.items():
            lowered = name.casefold()
            if "word" in lowered:
                word_entries.extend(entries)
            elif "phone" in lowered:
                phone_entries.extend(entries)

        segments: list[AlignmentSegment] = []
        words: list[AlignmentSegment] = []
        word_index = 0
        silence_index = 0
        for raw in sorted(word_entries, key=lambda value: float(value[0])):
            start_s, end_s, label = float(raw[0]), float(raw[1]), str(raw[2])
            if end_s <= start_s:
                continue
            start, end = _canonical_bounds(start_s, end_s, job)
            if not label.strip():
                segment = AlignmentSegment(
                    segment_id=f"{run_id}:{job.entry.id}:silence:{silence_index:06d}",
                    recording_id=job.entry.id,
                    tier="silence",
                    label="<silence>",
                    backend_label=label,
                    start_s=start_s,
                    end_s=end_s,
                    start_sample=start,
                    end_sample=end,
                    timebase_sample_rate_hz=job.prepared.canonical_sample_rate_hz,
                    source="mfa",
                    run_id=run_id,
                    model_id=model_id,
                )
                silence_index += 1
                segments.append(segment)
                continue
            segment = AlignmentSegment(
                segment_id=f"{run_id}:{job.entry.id}:word:{word_index:06d}",
                recording_id=job.entry.id,
                tier="word",
                label=label,
                backend_label=label,
                start_s=start_s,
                end_s=end_s,
                start_sample=start,
                end_sample=end,
                timebase_sample_rate_hz=job.prepared.canonical_sample_rate_hz,
                source_token_ids=source_token_ids_for_word(job.transcript, word_index),
                source="mfa",
                run_id=run_id,
                model_id=model_id,
            )
            word_index += 1
            words.append(segment)
            segments.append(segment)

        phone_index = 0
        for raw in sorted(phone_entries, key=lambda value: float(value[0])):
            start_s, end_s, label = float(raw[0]), float(raw[1]), str(raw[2])
            if end_s <= start_s or not label.strip():
                continue
            start, end = _canonical_bounds(start_s, end_s, job)
            parent = next(
                (
                    word
                    for word in words
                    if start >= word.start_sample - 1 and end <= word.end_sample + 1
                ),
                None,
            )
            segments.append(
                AlignmentSegment(
                    segment_id=f"{run_id}:{job.entry.id}:phone:{phone_index:06d}",
                    recording_id=job.entry.id,
                    tier="phone",
                    label=label,
                    backend_label=label,
                    start_s=start_s,
                    end_s=end_s,
                    start_sample=start,
                    end_sample=end,
                    timebase_sample_rate_hz=job.prepared.canonical_sample_rate_hz,
                    parent_segment_id=parent.segment_id if parent else None,
                    source_token_ids=parent.source_token_ids if parent else (),
                    source="mfa",
                    run_id=run_id,
                    model_id=model_id,
                )
            )
            phone_index += 1
        segments.insert(
            0,
            AlignmentSegment(
                segment_id=f"{run_id}:{job.entry.id}:utterance:000000",
                recording_id=job.entry.id,
                tier="utterance",
                label=job.transcript.normalized_text,
                backend_label=job.transcript.normalized_text,
                start_s=0.0,
                end_s=job.prepared.source_duration_s,
                start_sample=0,
                end_sample=job.prepared.canonical_frame_count,
                timebase_sample_rate_hz=job.prepared.canonical_sample_rate_hz,
                source="mfa",
                run_id=run_id,
                model_id=model_id,
            ),
        )
        result[job.entry.id] = tuple(segments)
    return result
