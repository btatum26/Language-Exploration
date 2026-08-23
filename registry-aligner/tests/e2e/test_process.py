from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from registry_align.alignment.models import (
    AlignmentJob,
    BackendDiagnostics,
    BackendResult,
)
from registry_align.config import AppConfig, DefaultsConfig, InputConfig
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.runs import ProcessRequest
from registry_align.domain.segments import AlignmentSegment, SegmentRevision
from registry_align.errors import DependencyError
from registry_align.events import Issue, Severity
from registry_align.pipeline.service import AlignmentService
from registry_align.storage.sqlite import SQLiteRepository


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def doctor(self) -> BackendDiagnostics:
        return BackendDiagnostics(usable=True, name="fake", version="1", capabilities=("align",))

    def fingerprint(self) -> str:
        return "fake-backend-v1"

    def align(self, jobs: tuple[AlignmentJob, ...], workspace: Path, run_id: str) -> BackendResult:
        self.calls += 1
        by_recording: dict[str, tuple[AlignmentSegment, ...]] = {}
        for job in jobs:
            rate = job.prepared.canonical_sample_rate_hz
            word_id = f"{run_id}:{job.entry.id}:word:000000"
            by_recording[job.entry.id] = (
                AlignmentSegment(
                    segment_id=f"{run_id}:{job.entry.id}:utterance:000000",
                    recording_id=job.entry.id,
                    tier="utterance",
                    label=job.transcript.normalized_text,
                    backend_label=job.transcript.normalized_text,
                    start_s=0,
                    end_s=1,
                    start_sample=0,
                    end_sample=rate,
                    timebase_sample_rate_hz=rate,
                    source="fake",
                    run_id=run_id,
                    model_id="fake",
                ),
                AlignmentSegment(
                    segment_id=f"{run_id}:{job.entry.id}:silence:000000",
                    recording_id=job.entry.id,
                    tier="silence",
                    label="<silence>",
                    backend_label="",
                    start_s=0,
                    end_s=0.1,
                    start_sample=0,
                    end_sample=rate // 10,
                    timebase_sample_rate_hz=rate,
                    source="fake",
                    run_id=run_id,
                    model_id="fake",
                ),
                AlignmentSegment(
                    segment_id=word_id,
                    recording_id=job.entry.id,
                    tier="word",
                    label="ciao",
                    backend_label="ciao",
                    start_s=0.1,
                    end_s=0.9,
                    start_sample=rate // 10,
                    end_sample=rate * 9 // 10,
                    timebase_sample_rate_hz=rate,
                    source_token_ids=(f"{job.entry.id}:source-token:000000",),
                    source="fake",
                    run_id=run_id,
                    model_id="fake",
                ),
                AlignmentSegment(
                    segment_id=f"{run_id}:{job.entry.id}:phone:000000",
                    recording_id=job.entry.id,
                    tier="phone",
                    label="tʃao",
                    backend_label="tʃao",
                    start_s=0.1,
                    end_s=0.9,
                    start_sample=rate // 10,
                    end_sample=rate * 9 // 10,
                    timebase_sample_rate_hz=rate,
                    parent_segment_id=word_id,
                    source_token_ids=(f"{job.entry.id}:source-token:000000",),
                    source="fake",
                    run_id=run_id,
                    model_id="fake",
                ),
            )
        raw = workspace / "backend" / "fake" / run_id
        raw.mkdir(parents=True, exist_ok=True)
        return BackendResult(
            segments=by_recording,
            raw_output_directory=raw,
            command=("fake-align",),
        )


def _fake_prepare(
    entry: Any, output: Path, config: Any, *, ffmpeg_version: str | None = None
) -> PreparedRecording:
    del config, ffmpeg_version
    canonical = output / "derived" / "pcm" / f"{entry.id}.wav"
    aligned = output / "derived" / "alignment-audio" / f"{entry.id}.wav"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    aligned.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"canonical")
    aligned.write_bytes(b"alignment")
    from registry_align.audio.probe import sha256_file

    return PreparedRecording(
        recording_id=entry.id,
        source_audio_sha256=sha256_file(entry.audio_resolved_path),
        source_codec="mp3",
        source_sample_rate_hz=1000,
        source_channels=1,
        source_duration_s=1,
        canonical_pcm_path=canonical,
        canonical_pcm_sha256=sha256_file(canonical),
        canonical_sample_rate_hz=1000,
        canonical_channels=1,
        canonical_frame_count=1000,
        alignment_audio_path=aligned,
        alignment_audio_sha256=sha256_file(aligned),
        alignment_sample_rate_hz=1000,
        alignment_channels=1,
        decoder_name="fake",
        decoder_version="1",
    )


def test_process_persists_exports_and_reuses_verified_cache(
    monkeypatch: Any, registry_factory: Any
) -> None:
    document = {
        "recordings": [
            {
                "id": "one",
                "audio": {"path": "audio/one.wav"},
                "transcript": {"text": "ciao", "language": "it"},
            }
        ]
    }
    registry = registry_factory(document)
    output = registry.parent / "alignment-output"
    backend = FakeBackend()
    config = AppConfig(
        input=InputConfig(
            entries_path="recordings",
            audio_path="audio.path",
            transcript_path="transcript.text",
            language_path="transcript.language",
        ),
        defaults=DefaultsConfig(language="it"),
    )
    service = AlignmentService(config, adapter_mode=True, backend=backend)
    monkeypatch.setattr(
        "registry_align.pipeline.service.executable_version", lambda value: "fake-1"
    )
    monkeypatch.setattr("registry_align.pipeline.service.prepare_recording", _fake_prepare)
    request = ProcessRequest(registry_path=registry, output_directory=output)

    first = service.process(request)
    second = service.process(request)

    assert first.status == "complete"
    assert first.counts["completed"] == 1
    assert second.status == "complete"
    assert second.counts["cached"] == 1
    assert backend.calls == 1

    (registry.parent / "audio" / "one.wav").write_bytes(b"changed source")
    third = service.process(request)

    assert third.counts["completed"] == 1
    assert third.counts["cached"] == 0
    assert backend.calls == 2
    assert (output / "corpus.sqlite3").is_file()
    assert (output / "run-manifest.json").is_file()
    assert (output / "recordings.jsonl").is_file()
    assert (output / "segments.jsonl").is_file()
    assert (output / "qc-report.json").is_file()
    repository = SQLiteRepository(output / "corpus.sqlite3")
    assert len(repository.effective_recordings()) == 1
    assert {row["tier"] for row in repository.effective_segments()} == {
        "utterance",
        "silence",
        "word",
        "phone",
    }

    word = next(row for row in repository.effective_segments() if row["tier"] == "word")
    repository.save_revision(
        SegmentRevision(
            revision_id="revision-one",
            segment_id=word["segment_id"],
            base_run_id=word["run_id"],
            start_sample_override=120,
            label_override="ciao-revised",
            review_status="accepted",
            created_at=datetime.now(UTC),
        )
    )
    revised = next(row for row in repository.effective_segments() if row["tier"] == "word")
    assert revised["model_start_sample"] == 100
    assert revised["start_sample"] == 120
    assert revised["label"] == "ciao-revised"
    assert revised["effective_revision_id"] == "revision-one"

    exported = AlignmentService.export(output, ("csv", "textgrid"))
    assert exported["files"]
    assert (output / "exports" / "textgrids" / "one.TextGrid").is_file()


def test_process_checks_all_dependencies_before_creating_output(
    monkeypatch: Any, registry_factory: Any
) -> None:
    class UnusableBackend(FakeBackend):
        def doctor(self) -> BackendDiagnostics:
            return BackendDiagnostics(
                usable=False,
                name="fake",
                issues=(
                    Issue(
                        code="MODEL_MISSING",
                        severity=Severity.ERROR,
                        stage="doctor",
                        message="model is missing",
                    ),
                ),
            )

    registry = registry_factory(
        {
            "recordings": [
                {
                    "id": "one",
                    "audio": {"path": "audio/one.wav"},
                    "transcript": {"text": "ciao", "language": "it"},
                }
            ]
        }
    )
    output = registry.parent / "must-not-exist"
    config = AppConfig(
        input=InputConfig(
            entries_path="recordings",
            audio_path="audio.path",
            transcript_path="transcript.text",
            language_path="transcript.language",
        )
    )
    service = AlignmentService(config, adapter_mode=True, backend=UnusableBackend())
    monkeypatch.setattr(
        "registry_align.pipeline.service.executable_version", lambda value: "fake-1"
    )

    with pytest.raises(DependencyError, match="model is missing"):
        service.process(ProcessRequest(registry_path=registry, output_directory=output))

    assert not output.exists()
