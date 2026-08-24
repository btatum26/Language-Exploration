"""Stable interface-independent application service."""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from registry_align.alignment.base import AlignerBackend
from registry_align.alignment.mfa.backend import MfaBackend
from registry_align.alignment.models import AlignmentJob
from registry_align.audio.decoder import prepare_recording
from registry_align.audio.probe import executable_version, sha256_file
from registry_align.config import AppConfig, load_config
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.runs import PlanResult, ProcessRequest, ProcessResult
from registry_align.domain.segments import SegmentRevision
from registry_align.errors import (
    ConfigurationError,
    DependencyError,
    InitializationError,
    ProcessingError,
)
from registry_align.events import Event, EventObserver, Issue, NullEventObserver, Severity
from registry_align.export.csv import export_csv
from registry_align.export.jsonl import export_jsonl
from registry_align.export.textgrid import export_textgrids
from registry_align.pipeline.cache import config_fingerprint, recording_cache_key
from registry_align.pipeline.planner import build_plan
from registry_align.qc.checks import check_alignment, check_stored_alignments
from registry_align.qc.reports import write_qc_reports
from registry_align.registry.loader import load_registry
from registry_align.registry.validation import RegistryValidationResult
from registry_align.storage.atomic import atomic_write_json, atomic_write_text
from registry_align.storage.sqlite import SQLiteRepository
from registry_align.text.normalizer import normalize_transcript

CONFIG_TEMPLATE = """schema_version = "1"

[input]
entries_path = "recordings"
id_path = "id"
audio_path = "audio.path"
transcript_path = "transcript.text"
language_path = "transcript.language"
speaker_path = "speaker_id"
metadata_paths = ["lesson_id", "category", "target_sound"]

[defaults]
language = "it"

[audio]
editor_pcm_mode = "native"
alignment_sample_rate_hz = 16000
alignment_channels = 1
ffmpeg_executable = "ffmpeg"
ffprobe_executable = "ffprobe"

[text]
normalizer = "italian"
unicode_form = "NFC"
lowercase = true

[alignment]
backend = "mfa"
jobs = 4
continue_on_error = true

[alignment.mfa]
executable = "mfa"
model_mode = "auto"
acoustic_model = "italian_cv"
dictionary = "italian_cv"
g2p_model = ""
use_g2p_for_oov = false
fine_tune = false
validate_corpus = true
dialect = ""

[output]
write_sqlite = true
write_jsonl = true
write_textgrids = false
retain_backend_output = true
"""


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{timestamp}_{uuid.uuid4().hex[:12]}"


class AlignmentService:
    """Application boundary shared by the CLI and future interfaces."""

    def __init__(
        self,
        config: AppConfig,
        *,
        adapter_mode: bool,
        observer: EventObserver | None = None,
        backend: AlignerBackend | None = None,
    ) -> None:
        self.config = config
        self.adapter_mode = adapter_mode
        self.observer = observer or NullEventObserver()
        self._injected_backend = backend

    @classmethod
    def from_config(
        cls, path: Path | None = None, *, observer: EventObserver | None = None
    ) -> AlignmentService:
        return cls(load_config(path), adapter_mode=path is not None, observer=observer)

    def _backend(self) -> AlignerBackend:
        if self._injected_backend is not None:
            return self._injected_backend
        if self.config.alignment.backend != "mfa":
            raise ConfigurationError(
                f"unsupported alignment backend: {self.config.alignment.backend}"
            )
        return MfaBackend(self.config.alignment)

    def validate_registry(
        self, registry_path: Path, *, fail_fast: bool = False
    ) -> RegistryValidationResult:
        return load_registry(
            registry_path,
            mapping=self.config.input if self.adapter_mode else None,
            defaults=self.config.defaults,
            fail_fast=fail_fast,
        )

    def plan(
        self,
        registry_path: Path,
        *,
        selected_ids: tuple[str, ...] = (),
        fail_fast: bool = False,
    ) -> PlanResult:
        validation = self.validate_registry(registry_path, fail_fast=fail_fast)
        return build_plan(registry_path, validation, selected_ids)

    def doctor(self, output_directory: Path | None = None) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "python": {"usable": True, "version": sys.version.split()[0]},
        }
        usable = True
        for name, executable in (
            ("ffmpeg", self.config.audio.ffmpeg_executable),
            ("ffprobe", self.config.audio.ffprobe_executable),
        ):
            try:
                checks[name] = {"usable": True, "version": executable_version(executable)}
            except DependencyError as exc:
                usable = False
                checks[name] = {
                    "usable": False,
                    "message": str(exc),
                    "remediation_commands": ("conda install -c conda-forge ffmpeg",),
                }
        backend = self._backend().doctor()
        checks["alignment_backend"] = backend.model_dump(mode="json")
        usable = usable and backend.usable
        if output_directory is not None:
            try:
                output_directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=output_directory, delete=True):
                    pass
                checks["output_directory"] = {"usable": True, "path": str(output_directory)}
            except OSError as exc:
                usable = False
                checks["output_directory"] = {
                    "usable": False,
                    "path": str(output_directory),
                    "message": str(exc),
                }
        return {"schema_version": "1.0", "usable": usable, "checks": checks}

    def _validate_request(self, request: ProcessRequest) -> tuple[RegistryEntry, ...]:
        validation = self.validate_registry(request.registry_path)
        if not validation.is_valid:
            summary = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.issues[:5])
            raise ConfigurationError(f"registry validation failed: {summary}")
        entries = validation.entries
        if request.selected_ids:
            selected = set(request.selected_ids)
            known = {entry.id for entry in entries}
            unknown = selected - known
            if unknown:
                raise ConfigurationError(f"selected recording IDs do not exist: {sorted(unknown)}")
            entries = tuple(entry for entry in entries if entry.id in selected)
        if not entries:
            raise ConfigurationError("no registry entries were selected")
        return entries

    def prepare(self, request: ProcessRequest) -> ProcessResult:
        return self._execute(request, align=False)

    def process(self, request: ProcessRequest) -> ProcessResult:
        return self._execute(request, align=True)

    def _execute(self, request: ProcessRequest, *, align: bool) -> ProcessResult:
        entries = self._validate_request(request)
        output = request.output_directory.resolve(strict=False)
        registry_root = request.registry_path.resolve(strict=False).parent
        if output == registry_root:
            raise ConfigurationError("output directory cannot be the registry source directory")
        for entry in entries:
            source = entry.audio_resolved_path
            if source.is_relative_to(output) or output.is_relative_to(source.parent):
                raise ConfigurationError(
                    f"output directory overlaps source audio location for {entry.id}"
                )
        backend: AlignerBackend | None = self._backend() if align else None
        if align:
            dependency_report = self.doctor()
            if not dependency_report["usable"]:
                messages = []
                for name, check in dependency_report["checks"].items():
                    if not check.get("usable", False):
                        message = check.get("message")
                        if message is None and check.get("issues"):
                            message = "; ".join(
                                str(issue.get("message", "unavailable"))
                                for issue in check["issues"]
                            )
                        messages.append(f"{name}: {message or 'unavailable'}")
                raise DependencyError("dependencies are not usable: " + "; ".join(messages))
        ffmpeg_version = executable_version(self.config.audio.ffmpeg_executable)
        executable_version(self.config.audio.ffprobe_executable)
        diagnostics = backend.doctor() if backend is not None else None
        backend_fingerprint = backend.fingerprint() if backend is not None else "prepare-only"
        backend_name = backend.name if backend is not None else "none"
        backend_version = diagnostics.version if diagnostics and diagnostics.version else "none"
        model_id = self.config.alignment.mfa.acoustic_model if backend is not None else "none"
        config_hash = config_fingerprint(self.config)
        output.mkdir(parents=True, exist_ok=True)
        database = SQLiteRepository(output / "corpus.sqlite3")
        database.migrate()
        run_id = _new_run_id()
        database.start_run(
            run_id,
            request.registry_path.resolve(strict=False),
            config_hash,
            backend_name,
            backend_version,
            model_id,
        )
        self.observer.on_event(
            Event(name="run_started", status="running", details={"run_id": run_id})
        )

        counts = {"selected": len(entries), "prepared": 0, "completed": 0, "cached": 0, "failed": 0}
        issues: list[Issue] = []
        jobs: list[AlignmentJob] = []
        cache_keys: dict[str, str] = {}
        prepared_by_id: dict[str, Any] = {}
        transcript_by_id: dict[str, Any] = {}
        for entry in entries:
            database.save_discovered_entry(entry, run_id)
            self.observer.on_event(Event(name="entry_started", recording_id=entry.id))
            try:
                transcript = normalize_transcript(
                    entry.id,
                    entry.transcript_raw,
                    profile=self.config.text.normalizer,
                    lowercase=self.config.text.lowercase,
                )
                source_hash = sha256_file(entry.audio_resolved_path)
                cache_key = recording_cache_key(
                    source_hash,
                    transcript,
                    self.config,
                    ffmpeg_version,
                    backend_fingerprint,
                )
                cached = (
                    database.get_cache(cache_key) if request.resume and not request.force else None
                )
                if (
                    align
                    and cached is not None
                    and cached.canonical_pcm_path.is_file()
                    and cached.alignment_audio_path.is_file()
                    and sha256_file(cached.canonical_pcm_path) == cached.canonical_pcm_sha256
                    and sha256_file(cached.alignment_audio_path) == cached.alignment_audio_sha256
                ):
                    database.point_recording_to_run(entry.id, cached.result_run_id)
                    counts["cached"] += 1
                    self.observer.on_event(
                        Event(name="entry_completed", recording_id=entry.id, status="cached")
                    )
                    continue
                prepared = prepare_recording(
                    entry,
                    output,
                    self.config.audio,
                    ffmpeg_version=ffmpeg_version,
                )
                database.save_recording(entry, prepared, run_id)
                database.save_transcript(run_id, transcript)
                database.save_artifact(
                    run_id,
                    "canonical_pcm",
                    prepared.canonical_pcm_path,
                    recording_id=entry.id,
                    sha256=prepared.canonical_pcm_sha256,
                    details={
                        "sample_rate_hz": prepared.canonical_sample_rate_hz,
                        "channels": prepared.canonical_channels,
                        "frame_count": prepared.canonical_frame_count,
                    },
                )
                database.save_artifact(
                    run_id,
                    "alignment_audio",
                    prepared.alignment_audio_path,
                    recording_id=entry.id,
                    sha256=prepared.alignment_audio_sha256,
                    details={
                        "sample_rate_hz": prepared.alignment_sample_rate_hz,
                        "channels": prepared.alignment_channels,
                    },
                )
                prepared_by_id[entry.id] = prepared
                transcript_by_id[entry.id] = transcript
                cache_keys[entry.id] = cache_key
                counts["prepared"] += 1
                if align:
                    jobs.append(AlignmentJob(entry=entry, prepared=prepared, transcript=transcript))
                else:
                    self.observer.on_event(
                        Event(name="entry_completed", recording_id=entry.id, status="prepared")
                    )
            except (DependencyError, ProcessingError, OSError) as exc:
                issue = Issue(
                    code="ENTRY_PREPARATION_FAILED",
                    severity=Severity.ERROR,
                    stage="prepare",
                    recording_id=entry.id,
                    run_id=run_id,
                    message=str(exc),
                )
                issues.append(issue)
                database.save_issue(run_id, issue)
                counts["failed"] += 1
                if not self.config.alignment.continue_on_error:
                    database.finish_run(run_id, "failed", counts)
                    raise ProcessingError(str(exc)) from exc

        command: tuple[str, ...] = ()
        if align and jobs:
            try:
                backend_result = backend.align(tuple(jobs), output, run_id) if backend else None
                assert backend_result is not None
                command = backend_result.command
                if backend_result.stdout or backend_result.stderr:
                    atomic_write_text(
                        output / "logs" / f"{run_id}.log",
                        f"STDOUT\n{backend_result.stdout}\nSTDERR\n{backend_result.stderr}\n",
                    )
                for issue in backend_result.issues:
                    issues.append(issue)
                    database.save_issue(run_id, issue)
                for job in jobs:
                    segments = backend_result.segments.get(job.entry.id, ())
                    if not segments:
                        issue = Issue(
                            code="ALIGNMENT_RESULT_MISSING",
                            severity=Severity.ERROR,
                            stage="import",
                            recording_id=job.entry.id,
                            run_id=run_id,
                            message="backend produced no canonical segments",
                        )
                        issues.append(issue)
                        database.save_issue(run_id, issue)
                        counts["failed"] += 1
                        continue
                    database.save_segments(segments)
                    qc_issues = check_alignment(
                        run_id,
                        prepared_by_id[job.entry.id],
                        transcript_by_id[job.entry.id],
                        segments,
                    )
                    for issue in qc_issues:
                        issues.append(issue)
                        database.save_issue(run_id, issue)
                    database.save_cache(
                        cache_keys[job.entry.id],
                        run_id,
                        prepared_by_id[job.entry.id],
                        backend_fingerprint,
                    )
                    counts["completed"] += 1
                    self.observer.on_event(
                        Event(name="entry_completed", recording_id=job.entry.id, status="completed")
                    )
            except (DependencyError, ProcessingError, ConfigurationError) as exc:
                for job in jobs:
                    issue = Issue(
                        code="ALIGNMENT_PARTITION_FAILED",
                        severity=Severity.ERROR,
                        stage="align",
                        recording_id=job.entry.id,
                        run_id=run_id,
                        message=str(exc),
                    )
                    issues.append(issue)
                    database.save_issue(run_id, issue)
                counts["failed"] += len(jobs)
        elif not align:
            counts["completed"] = counts["prepared"]

        if counts["failed"] and (counts["completed"] or counts["cached"]):
            status = "partial"
        elif counts["failed"]:
            status = "failed"
        else:
            status = "complete"
        database.finish_run(run_id, status, counts)
        recordings = database.effective_recordings()
        effective_segments = database.effective_segments()
        stored_issues = database.effective_issues()
        output_locations: dict[str, Path] = {"database": database.path}
        if self.config.output.write_jsonl:
            recordings_path, segments_path = export_jsonl(output, recordings, effective_segments)
            output_locations.update(
                {"recordings_jsonl": recordings_path, "segments_jsonl": segments_path}
            )
        if self.config.output.write_textgrids:
            paths = export_textgrids(output, effective_segments)
            if paths:
                output_locations["textgrids"] = paths[0].parent
        qc_json, qc_csv = write_qc_reports(output, stored_issues)
        output_locations.update({"qc_json": qc_json, "qc_csv": qc_csv})
        manifest_path = output / "run-manifest.json"
        atomic_write_json(
            manifest_path,
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "status": status,
                "registry_path": str(request.registry_path.resolve(strict=False)),
                "output_directory": str(output),
                "counts": counts,
                "config_fingerprint": config_hash,
                "backend": {
                    "name": backend_name,
                    "version": backend_version,
                    "fingerprint": backend_fingerprint,
                    "model_id": model_id,
                    "command": list(command),
                },
            },
        )
        output_locations["manifest"] = manifest_path
        self.observer.on_event(Event(name="run_completed", status=status, details=counts))
        return ProcessResult(
            run_id=run_id,
            status=status,
            counts=counts,
            issues=tuple(issues),
            output_locations=output_locations,
        )

    @staticmethod
    def status(output_directory: Path) -> dict[str, Any]:
        repository = SQLiteRepository(output_directory / "corpus.sqlite3")
        if not repository.path.is_file():
            raise ProcessingError(f"alignment database does not exist: {repository.path}")
        run = repository.latest_run()
        issues = repository.effective_issues()
        counts = repository.status_counts()
        counts["review_needed"] = sum(issue["severity"] in ("warning", "error") for issue in issues)
        return {
            "schema_version": "1.0",
            "status": run["status"] if run else "empty",
            "run": run,
            "counts": counts,
        }

    @staticmethod
    def export(output_directory: Path, formats: tuple[str, ...]) -> dict[str, Any]:
        repository = SQLiteRepository(output_directory / "corpus.sqlite3")
        if not repository.path.is_file():
            raise ProcessingError(f"alignment database does not exist: {repository.path}")
        recordings = repository.effective_recordings()
        segments = repository.effective_segments()
        issues = repository.effective_issues()
        locations: list[str] = []
        if "jsonl" in formats:
            locations.extend(
                str(path) for path in export_jsonl(output_directory, recordings, segments)
            )
        if "csv" in formats:
            locations.extend(
                str(path) for path in export_csv(output_directory, recordings, segments, issues)
            )
        if "textgrid" in formats:
            locations.extend(str(path) for path in export_textgrids(output_directory, segments))
        return {"schema_version": "1.0", "formats": list(formats), "files": locations}

    @staticmethod
    def qc(output_directory: Path) -> dict[str, Any]:
        repository = SQLiteRepository(output_directory / "corpus.sqlite3")
        if not repository.path.is_file():
            raise ProcessingError(f"alignment database does not exist: {repository.path}")
        run = repository.latest_run()
        run_id = str(run["run_id"]) if run else ""
        preserved = [issue for issue in repository.effective_issues() if issue["stage"] != "qc"]
        issues = [
            *preserved,
            *check_stored_alignments(
                run_id,
                repository.effective_recordings(),
                repository.effective_segments(),
            ),
        ]
        paths = write_qc_reports(output_directory, issues)
        return {
            "schema_version": "1.0",
            "issues": len(issues),
            "files": [str(path) for path in paths],
        }

    @staticmethod
    def save_revision(output_directory: Path, revision: SegmentRevision) -> None:
        repository = SQLiteRepository(output_directory / "corpus.sqlite3")
        if not repository.path.is_file():
            raise ProcessingError(f"alignment database does not exist: {repository.path}")
        repository.save_revision(revision)

    @staticmethod
    def initialize(directory: Path, *, force: bool = False) -> tuple[Path, Path]:
        from registry_align.schemas import canonical_registry_schema

        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / "registry-align.toml"
        schema_path = directory / "canonical-registry.schema.json"
        existing = [path for path in (config_path, schema_path) if path.exists()]
        if existing and not force:
            names = ", ".join(path.name for path in existing)
            raise InitializationError(f"refusing to overwrite existing file(s): {names}")
        try:
            config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
            schema_path.write_text(
                json.dumps(canonical_registry_schema(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except OSError as exc:
            raise InitializationError(f"cannot initialize {directory}: {exc}") from exc
        return config_path, schema_path
