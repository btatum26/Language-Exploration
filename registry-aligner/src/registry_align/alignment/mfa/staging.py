"""Deterministic MFA corpus staging."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from registry_align.alignment.models import AlignmentJob
from registry_align.storage.atomic import atomic_write_json


@dataclass(frozen=True)
class StagingEntry:
    recording_id: str
    relative_stem: str


def _safe_component(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def stage_jobs(jobs: tuple[AlignmentJob, ...], corpus: Path) -> tuple[StagingEntry, ...]:
    corpus.mkdir(parents=True, exist_ok=True)
    manifest: list[StagingEntry] = []
    for job in jobs:
        speaker = (
            f"speaker-{_safe_component(job.entry.speaker_id)}"
            if job.entry.speaker_id
            else f"recording-{_safe_component(job.entry.id)}"
        )
        stem = f"entry-{job.entry.source_entry_index:06d}-{_safe_component(job.entry.id)}"
        relative_stem = f"{speaker}/{stem}"
        directory = corpus / speaker
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(job.prepared.alignment_audio_path, directory / f"{stem}.wav")
        (directory / f"{stem}.lab").write_text(
            job.transcript.normalized_text + "\n", encoding="utf-8", newline="\n"
        )
        manifest.append(StagingEntry(job.entry.id, relative_stem))
    atomic_write_json(
        corpus.parent / "staging-manifest.json",
        {
            "schema_version": "1.0",
            "entries": [
                {"recording_id": item.recording_id, "relative_stem": item.relative_stem}
                for item in manifest
            ],
        },
    )
    return tuple(manifest)


def load_manifest(path: Path) -> tuple[StagingEntry, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(StagingEntry(**item) for item in payload["entries"])
