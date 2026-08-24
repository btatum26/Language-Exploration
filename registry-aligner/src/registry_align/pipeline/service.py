"""Service boundary for PostgreSQL metadata and remote source audio."""

from __future__ import annotations

import shutil
import socket
import sys
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from alembic.util.exc import CommandError
from sqlalchemy import make_url, text
from sqlalchemy.exc import SQLAlchemyError

from registry_align.alignment.base import AlignerBackend
from registry_align.alignment.mfa.backend import MfaBackend
from registry_align.alignment.models import AlignmentJob
from registry_align.artifacts import ArtifactStore, LocalArtifactStore
from registry_align.audio.probe import executable_version
from registry_align.config import AppConfig, database_admin_url, database_url, load_config
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.runs import (
    PlanResult,
    ProcessItem,
    ProcessRequest,
    ProcessResult,
)
from registry_align.errors import (
    ConfigurationError,
    DatabaseError,
    DependencyError,
    ProcessingError,
)
from registry_align.events import Event, EventObserver, Issue, NullEventObserver, Severity
from registry_align.export.csv import export_csv
from registry_align.export.jsonl import export_jsonl
from registry_align.export.textgrid import export_textgrids
from registry_align.pipeline.cache import (
    config_fingerprint,
    content_fingerprint,
    processing_fingerprint,
)
from registry_align.pipeline.planner import build_plan
from registry_align.qc.checks import check_alignment, check_stored_alignments
from registry_align.registry.loader import load_registry
from registry_align.registry.validation import RegistryValidationResult
from registry_align.remote_audio import (
    AudioObjectStore,
    AudioUploadRequest,
    RemoteAudioObject,
    SshAudioObjectStore,
)
from registry_align.storage.database import (
    DatabaseRuntime,
    check_database,
    create_database_engine,
    redact_secrets,
)
from registry_align.storage.postgres import PostgresUnitOfWorkFactory
from registry_align.storage.repository import UnitOfWorkFactory
from registry_align.storage.schema import schema_status, upgrade_schema
from registry_align.storage.tables import metadata
from registry_align.text.normalizer import normalize_transcript


class AlignmentService:
    """Interface-independent application service."""

    def __init__(
        self,
        config: AppConfig,
        *,
        observer: EventObserver | None = None,
        backend: AlignerBackend | None = None,
        uow_factory: UnitOfWorkFactory | None = None,
        artifact_store: ArtifactStore | None = None,
        audio_object_store: AudioObjectStore | None = None,
        adapter_mode: bool = True,
    ) -> None:
        self.config = config
        self.observer = observer or NullEventObserver()
        self._injected_backend = backend
        self._injected_uow_factory = uow_factory
        self.artifact_store = artifact_store or LocalArtifactStore(config.artifacts.root)
        self.audio_object_store = audio_object_store or SshAudioObjectStore(config.remote_audio)
        self.adapter_mode = adapter_mode

    @classmethod
    def from_config(
        cls, path: Path | None = None, *, observer: EventObserver | None = None
    ) -> AlignmentService:
        return cls(load_config(path), observer=observer, adapter_mode=path is not None)

    @contextmanager
    def _uows(self, *, require_schema: bool = True) -> Iterator[UnitOfWorkFactory]:
        if self._injected_uow_factory is not None:
            yield self._injected_uow_factory
            return
        try:
            with DatabaseRuntime(self.config.database) as engine:
                if require_schema:
                    status = schema_status(engine)
                    if not status["current_is_head"]:
                        raise DatabaseError(
                            "database schema is not current; run `registry-align db upgrade`"
                        )
                yield PostgresUnitOfWorkFactory(engine)
        except SQLAlchemyError as exc:
            raise DatabaseError(f"PostgreSQL operation failed: {redact_secrets(str(exc))}") from exc

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
        effective_path = registry_path
        if not registry_path.is_absolute() and not registry_path.exists():
            effective_path = self.config.source.root / registry_path
        return load_registry(
            effective_path,
            mapping=self.config.input if self.adapter_mode else None,
            defaults=self.config.defaults,
            fail_fast=fail_fast,
        )

    def _validated_entries(self, request: ProcessRequest) -> tuple[RegistryEntry, ...]:
        validation = self.validate_registry(request.registry_path)
        if not validation.is_valid:
            summary = "; ".join(f"{item.code}: {item.message}" for item in validation.issues[:5])
            raise ConfigurationError(f"registry validation failed: {summary}")
        entries = validation.entries
        if request.selected_ids:
            selected = set(request.selected_ids)
            unknown = selected - {entry.id for entry in entries}
            if unknown:
                raise ConfigurationError(f"selected recording IDs do not exist: {sorted(unknown)}")
            entries = tuple(entry for entry in entries if entry.id in selected)
        if not entries:
            raise ConfigurationError("no registry entries were selected")
        return entries

    def plan(
        self,
        registry_path: Path,
        *,
        selected_ids: tuple[str, ...] = (),
        fail_fast: bool = False,
    ) -> PlanResult:
        validation = self.validate_registry(registry_path, fail_fast=fail_fast)
        base = build_plan(registry_path, validation, selected_ids)
        ids = tuple(item.recording_id for item in base.items if item.status == "new")
        with self._uows() as factory, factory() as uow:
            existing = uow.repository.existing_recording_ids(ids)
        items = tuple(
            item.model_copy(update={"status": "existing"})
            if item.recording_id in existing and item.status == "new"
            else item
            for item in base.items
        )
        status_counts = Counter(item.status for item in items)
        counts = dict(base.counts)
        for status_name in ("new", "existing", "ignored", "blocked"):
            counts[status_name] = status_counts.get(status_name, 0)
        return PlanResult(
            registry_path=base.registry_path,
            items=items,
            issues=base.issues,
            counts=counts,
        )

    def db_check(self) -> dict[str, Any]:
        if self._injected_uow_factory is not None:
            return {"usable": True, "injected": True}
        with DatabaseRuntime(self.config.database) as engine:
            result = check_database(engine)
            result["revision"] = schema_status(engine)
            return result

    def db_upgrade(self) -> dict[str, Any]:
        if self._injected_uow_factory is not None:
            raise ConfigurationError("cannot migrate an injected repository")
        grant: str | None = None
        try:
            with DatabaseRuntime(
                self.config.database, connection_url=database_admin_url()
            ) as engine:
                details = check_database(engine)
                grant = f"GRANT CREATE ON SCHEMA {details['schema']} TO {details['user']};"
                upgrade_schema(engine)
                app_user = make_url(database_url()).username
                if app_user:
                    quote = engine.dialect.identifier_preparer.quote
                    schema = quote(str(details["schema"]))
                    role = quote(app_user)
                    with engine.begin() as connection:
                        connection.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {role}"))
                        connection.execute(
                            text(
                                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                                f"IN SCHEMA {schema} TO {role}"
                            )
                        )
                        connection.execute(
                            text(
                                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES "
                                f"IN SCHEMA {schema} TO {role}"
                            )
                        )
                        connection.execute(
                            text(
                                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
                            )
                        )
                        connection.execute(
                            text(
                                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {role}"
                            )
                        )
                return schema_status(engine)
        except SQLAlchemyError as exc:
            message = redact_secrets(str(exc))
            if "permission denied for schema" in message and grant is not None:
                raise DatabaseError(
                    "database migration requires CREATE on the target schema; ask its owner to run "
                    f"`{grant}`"
                ) from exc
            raise DatabaseError(f"database migration failed: {message}") from exc

    def db_create_development(self) -> str:
        if self._injected_uow_factory is not None:
            raise ConfigurationError("cannot create a database for an injected repository")
        with DatabaseRuntime(self.config.database, connection_url=database_admin_url()) as engine:
            target = make_url(database_url()).database or ""
            if not target.endswith("_dev") or not target.replace("_", "").isalnum():
                raise ConfigurationError(
                    "development database name must end in `_dev` and contain only "
                    "letters, digits, underscores"
                )
            admin_url = engine.url.set(database="postgres").render_as_string(hide_password=False)
            admin = create_database_engine(admin_url, self.config.database)
            try:
                with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    exists = connection.execute(
                        text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": target}
                    ).scalar_one_or_none()
                    if exists is None:
                        connection.execute(text(f'CREATE DATABASE "{target}"'))
            finally:
                admin.dispose()
            return target

    def db_reset_test(self) -> dict[str, Any]:
        if self._injected_uow_factory is not None:
            raise ConfigurationError("cannot reset a database for an injected repository")
        target = make_url(database_url()).database or ""
        admin_target = (
            make_url(database_admin_url())
            .set(database=target)
            .render_as_string(hide_password=False)
        )
        with DatabaseRuntime(self.config.database, connection_url=admin_target) as engine:
            if not target.endswith("_test"):
                raise ConfigurationError("refusing reset: database name must end in `_test`")
            with engine.begin() as connection:
                metadata.drop_all(connection, checkfirst=True)
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            upgrade_schema(engine)
            return schema_status(engine)

    def doctor(self, registry_path: Path | None = None) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "configuration": {"usable": True, "schema_version": self.config.schema_version},
            "python": {"usable": sys.version_info >= (3, 11), "version": sys.version.split()[0]},
        }
        for name, executable in (
            ("ffmpeg", self.config.audio.ffmpeg_executable),
            ("ffprobe", self.config.audio.ffprobe_executable),
        ):
            try:
                checks[name] = {"usable": True, "version": executable_version(executable)}
            except DependencyError as exc:
                checks[name] = {"usable": False, "message": str(exc)}
        backend = self._backend().doctor()
        checks["alignment_backend"] = backend.model_dump(mode="json")
        try:
            checks["database"] = self.db_check()
            if not checks["database"].get("revision", {}).get("current_is_head", True):
                checks["database"]["usable"] = False
        except (DatabaseError, CommandError) as exc:
            checks["database"] = {"usable": False, "message": redact_secrets(str(exc))}
        try:
            root = self.config.artifacts.root
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
            checks["artifact_cache"] = {"usable": True, **self.artifact_store.status()}
        except OSError as exc:
            checks["artifact_cache"] = {"usable": False, "message": str(exc)}
        try:
            checks["remote_audio"] = self.audio_object_store.status()
        except (DependencyError, ProcessingError) as exc:
            checks["remote_audio"] = {"usable": False, "message": str(exc)}
        source_root = self.config.source.root.resolve(strict=False)
        checks["source_root"] = {
            "usable": source_root.is_dir(),
            "path": str(source_root),
            **({} if source_root.is_dir() else {"message": "source root is not a directory"}),
        }
        if registry_path is not None:
            validation = self.validate_registry(registry_path)
            checks["source_registry"] = {
                "usable": validation.is_valid,
                "entries": len(validation.entries),
                "issues": [item.model_dump(mode="json") for item in validation.issues],
            }
        usable = all(bool(item.get("usable")) for item in checks.values())
        return {"usable": usable, "checks": checks}

    def process(self, request: ProcessRequest) -> ProcessResult:
        entries = self._validated_entries(request)
        return self.process_entries(entries, overwrite_existing=request.overwrite_existing)

    def process_entries(
        self,
        entries: tuple[RegistryEntry, ...],
        *,
        overwrite_existing: bool = False,
    ) -> ProcessResult:
        """Process already-validated entries for native and other application clients."""

        if not entries:
            raise ConfigurationError("no registry entries were selected")
        request = ProcessRequest(
            registry_path=entries[0].source_registry_path,
            overwrite_existing=overwrite_existing,
            selected_ids=tuple(item.id for item in entries),
        )
        with self._uows() as factory:
            if not overwrite_existing:
                with factory() as uow:
                    existing = uow.repository.existing_recording_ids(
                        tuple(item.id for item in entries)
                    )
                if len(existing) == len(entries):
                    return self._complete_all_skipped(entries, factory)
            backend = self._backend()
            diagnostics = backend.doctor()
            if not diagnostics.usable:
                message = "; ".join(item.message for item in diagnostics.issues)
                raise DependencyError(message or "alignment backend is unavailable")
            backend_fingerprint = backend.fingerprint()
            backend_version = diagnostics.version or "unknown"
            backend_summary = {
                "name": backend.name,
                "version": backend_version,
                "acoustic_model": self.config.alignment.mfa.acoustic_model,
                "dictionary": self.config.alignment.mfa.dictionary,
            }
            return self._process_with_factory(
                request, entries, backend, backend_fingerprint, backend_summary, factory
            )

    def _complete_all_skipped(
        self,
        entries: tuple[RegistryEntry, ...],
        factory: UnitOfWorkFactory,
    ) -> ProcessResult:
        backend_summary = {
            "name": self.config.alignment.backend,
            "version": "not-invoked",
            "acoustic_model": self.config.alignment.mfa.acoustic_model,
            "dictionary": self.config.alignment.mfa.dictionary,
        }
        items = tuple(
            ProcessItem(
                recording_id=entry.id,
                outcome="skipped",
                detail="logical recording already exists; current database version was trusted",
            )
            for entry in entries
        )
        with factory() as uow:
            run_id = uow.repository.start_run(
                config_fingerprint(self.config), backend_summary, socket.gethostname()
            )
            for item in items:
                uow.repository.save_run_item(
                    run_id,
                    item.recording_id,
                    item.outcome,
                    detail=item.detail,
                )
            uow.repository.finish_run(run_id, "complete", {"skipped": len(items)}, [])
            uow.commit()
        self.observer.on_event(
            Event(name="run_started", status="running", details={"run_id": str(run_id)})
        )
        self.observer.on_event(
            Event(name="run_finished", status="complete", details={"run_id": str(run_id)})
        )
        return ProcessResult(
            run_id=run_id,
            status="complete",
            counts={"skipped": len(items)},
            items=items,
            issues=(),
        )

    def _process_with_factory(
        self,
        request: ProcessRequest,
        entries: tuple[RegistryEntry, ...],
        backend: AlignerBackend,
        backend_fingerprint: str,
        backend_summary: dict[str, str],
        factory: UnitOfWorkFactory,
    ) -> ProcessResult:
        with factory() as uow:
            existing = uow.repository.existing_recording_ids(tuple(item.id for item in entries))
            run_id = uow.repository.start_run(
                config_fingerprint(self.config), backend_summary, socket.gethostname()
            )
            uow.commit()
        self.observer.on_event(
            Event(name="run_started", status="running", details={"run_id": str(run_id)})
        )
        workspace = self.config.artifacts.root.resolve(strict=False) / "work" / str(run_id)

        candidates = tuple(
            entry for entry in entries if request.overwrite_existing or entry.id not in existing
        )
        prepared_uploads = self._prepare_and_upload(candidates, workspace)

        items: dict[str, ProcessItem] = {}
        issues: list[Issue] = []
        jobs: list[AlignmentJob] = []
        claims: dict[str, UUID] = {}
        prepared_by_id: dict[str, Any] = {}
        transcript_by_id: dict[str, Any] = {}
        lease_owner = f"{socket.gethostname()}:{run_id}"
        abort_remaining = False

        for entry in entries:
            if abort_remaining:
                detail = "not attempted after an earlier fail-fast ingestion error"
                item = ProcessItem(recording_id=entry.id, outcome="failed", detail=detail)
                items[entry.id] = item
                with factory() as uow:
                    uow.repository.save_run_item(run_id, entry.id, "failed", detail=detail)
                    uow.commit()
                continue
            if entry.id in existing and not request.overwrite_existing:
                item = ProcessItem(
                    recording_id=entry.id,
                    outcome="skipped",
                    detail="logical recording already exists; current database version was trusted",
                )
                items[entry.id] = item
                with factory() as uow:
                    uow.repository.save_run_item(run_id, entry.id, item.outcome, detail=item.detail)
                    uow.commit()
                continue
            try:
                prepared_upload = prepared_uploads[entry.id]
                if isinstance(prepared_upload, Exception):
                    raise prepared_upload
                transcript, prepared, remote_audio = prepared_upload
                state_fingerprint = content_fingerprint(
                    entry, prepared.source_audio_sha256, transcript
                )
                with factory() as uow:
                    ingestion = uow.repository.ingest_version(
                        entry,
                        prepared,
                        transcript,
                        remote_storage_key=remote_audio.storage_key,
                        corpus_id=self.config.source.corpus_id,
                        content_fingerprint=state_fingerprint,
                        ingestion_reason="explicit-overwrite"
                        if request.overwrite_existing
                        else "initial-ingestion",
                        overwrite_existing=request.overwrite_existing,
                    )
                    uow.commit()
                if ingestion.state == "skipped":
                    item = ProcessItem(recording_id=entry.id, outcome="skipped")
                    items[entry.id] = item
                    with factory() as uow:
                        uow.repository.save_run_item(run_id, entry.id, "skipped")
                        uow.commit()
                    continue
                if ingestion.state == "unchanged":
                    item = ProcessItem(
                        recording_id=entry.id,
                        outcome="unchanged",
                        recording_version_id=ingestion.recording_version_id,
                    )
                    items[entry.id] = item
                    with factory() as uow:
                        uow.repository.save_run_item(
                            run_id,
                            entry.id,
                            "unchanged",
                            version_id=ingestion.recording_version_id,
                        )
                        uow.commit()
                    continue
                normalizer_identity = (
                    f"{transcript.normalizer_name}:{transcript.normalizer_version}"
                )
                process_hash = processing_fingerprint(
                    backend_fingerprint=backend_fingerprint,
                    normalizer_identity=normalizer_identity,
                    audio_profile=prepared.preparation_fingerprint,
                    pipeline_version=self.config.alignment.pipeline_version,
                )
                provenance = {
                    "backend_name": backend.name,
                    "backend_version": backend_summary["version"],
                    "acoustic_model": self.config.alignment.mfa.acoustic_model,
                    "dictionary_identity": self.config.alignment.mfa.dictionary,
                    "normalizer_identity": normalizer_identity,
                    "audio_profile": prepared.preparation_fingerprint,
                    "pipeline_version": self.config.alignment.pipeline_version,
                }
                with factory() as uow:
                    claim = uow.repository.claim_alignment(
                        ingestion.recording_version_id,
                        process_hash,
                        provenance,
                        lease_owner=lease_owner,
                        lease_expires_at=datetime.now(UTC)
                        + timedelta(seconds=self.config.alignment.claim_lease_seconds),
                    )
                    uow.commit()
                if claim.state in {"complete", "busy"}:
                    detail = (
                        "identical alignment result reused"
                        if claim.state == "complete"
                        else "identical alignment is currently leased by another worker"
                    )
                    item = ProcessItem(
                        recording_id=entry.id,
                        outcome="alignment-reused",
                        recording_version_id=ingestion.recording_version_id,
                        alignment_result_id=claim.alignment_result_id,
                        detail=detail,
                    )
                    items[entry.id] = item
                    with factory() as uow:
                        uow.repository.save_run_item(
                            run_id,
                            entry.id,
                            item.outcome,
                            version_id=ingestion.recording_version_id,
                            alignment_id=claim.alignment_result_id,
                            detail=detail,
                        )
                        uow.commit()
                    continue
                jobs.append(AlignmentJob(entry=entry, prepared=prepared, transcript=transcript))
                claims[entry.id] = claim.alignment_result_id
                prepared_by_id[entry.id] = prepared
                transcript_by_id[entry.id] = transcript
                items[entry.id] = ProcessItem(
                    recording_id=entry.id,
                    outcome="version-created",
                    recording_version_id=ingestion.recording_version_id,
                    alignment_result_id=claim.alignment_result_id,
                )
                with factory() as uow:
                    uow.repository.save_run_item(
                        run_id,
                        entry.id,
                        "version-created",
                        version_id=ingestion.recording_version_id,
                        alignment_id=claim.alignment_result_id,
                    )
                    uow.commit()
            except Exception as exc:
                if not isinstance(
                    exc,
                    (
                        ConfigurationError,
                        DependencyError,
                        ProcessingError,
                        OSError,
                        SQLAlchemyError,
                    ),
                ):
                    raise
                issue = Issue(
                    code="ENTRY_INGESTION_FAILED",
                    severity=Severity.ERROR,
                    stage="ingest",
                    recording_id=entry.id,
                    run_id=str(run_id),
                    message=redact_secrets(str(exc)),
                )
                issues.append(issue)
                items[entry.id] = ProcessItem(
                    recording_id=entry.id, outcome="failed", detail=issue.message
                )
                with factory() as uow:
                    uow.repository.save_issue(run_id, issue)
                    uow.repository.save_run_item(run_id, entry.id, "failed", detail=issue.message)
                    uow.commit()
                if not self.config.alignment.continue_on_error:
                    abort_remaining = True

        if jobs:
            try:
                result = backend.align(tuple(jobs), workspace, str(run_id))
            except (
                ConfigurationError,
                DependencyError,
                ProcessingError,
                OSError,
                SQLAlchemyError,
            ) as exc:
                message = redact_secrets(str(exc))
                for job in jobs:
                    alignment_id = claims[job.entry.id]
                    prior = items[job.entry.id]
                    issue = Issue(
                        code="ALIGNMENT_FAILED",
                        severity=Severity.ERROR,
                        stage="align",
                        recording_id=job.entry.id,
                        run_id=str(run_id),
                        message=message,
                    )
                    issues.append(issue)
                    with factory() as uow:
                        uow.repository.fail_alignment(alignment_id, {"error": message})
                        uow.repository.save_issue(run_id, issue)
                        uow.repository.save_run_item(
                            run_id,
                            job.entry.id,
                            "failed",
                            version_id=prior.recording_version_id,
                            alignment_id=alignment_id,
                            detail=message,
                        )
                        uow.commit()
                    items[job.entry.id] = prior.model_copy(
                        update={"outcome": "failed", "detail": message}
                    )
            else:
                for issue in result.issues:
                    issues.append(issue)
                    with factory() as uow:
                        uow.repository.save_issue(run_id, issue)
                        uow.commit()
                for job in jobs:
                    alignment_id = claims[job.entry.id]
                    prior = items[job.entry.id]
                    try:
                        model_segments = result.segments.get(job.entry.id, ())
                        if not model_segments:
                            raise ProcessingError(
                                f"backend produced no segments for {job.entry.id}"
                            )
                        qc_issues = check_alignment(
                            str(run_id),
                            prepared_by_id[job.entry.id],
                            transcript_by_id[job.entry.id],
                            model_segments,
                        )
                        issues.extend(qc_issues)
                        qc_summary = {
                            "issue_count": len(qc_issues),
                            "errors": sum(item.severity == Severity.ERROR for item in qc_issues),
                            "warnings": sum(
                                item.severity == Severity.WARNING for item in qc_issues
                            ),
                        }
                        with factory() as uow:
                            uow.repository.complete_alignment(
                                alignment_id, model_segments, qc_summary
                            )
                            for issue in qc_issues:
                                uow.repository.save_issue(run_id, issue)
                            uow.repository.save_run_item(
                                run_id,
                                job.entry.id,
                                "processed",
                                version_id=prior.recording_version_id,
                                alignment_id=alignment_id,
                            )
                            uow.commit()
                        items[job.entry.id] = prior.model_copy(update={"outcome": "processed"})
                    except (ProcessingError, OSError, SQLAlchemyError) as exc:
                        message = redact_secrets(str(exc))
                        issue = Issue(
                            code="ALIGNMENT_IMPORT_FAILED",
                            severity=Severity.ERROR,
                            stage="import",
                            recording_id=job.entry.id,
                            run_id=str(run_id),
                            message=message,
                        )
                        issues.append(issue)
                        with factory() as uow:
                            uow.repository.fail_alignment(alignment_id, {"error": message})
                            uow.repository.save_issue(run_id, issue)
                            uow.repository.save_run_item(
                                run_id,
                                job.entry.id,
                                "failed",
                                version_id=prior.recording_version_id,
                                alignment_id=alignment_id,
                                detail=message,
                            )
                            uow.commit()
                        items[job.entry.id] = prior.model_copy(
                            update={"outcome": "failed", "detail": message}
                        )
        root = self.config.artifacts.root.resolve(strict=False)
        if workspace.resolve(strict=False).is_relative_to(root):
            shutil.rmtree(workspace, ignore_errors=True)

        counts = {
            str(key): value
            for key, value in Counter(item.outcome for item in items.values()).items()
        }
        failures = counts.get("failed", 0)
        status: Literal["complete", "partial", "failed"] = (
            "failed" if failures == len(items) else "partial" if failures else "complete"
        )
        errors = [
            item.model_dump(mode="json") for item in issues if item.severity == Severity.ERROR
        ]
        with factory() as uow:
            uow.repository.finish_run(run_id, status, counts, errors)
            uow.commit()
        self.observer.on_event(
            Event(name="run_finished", status=status, details={"run_id": str(run_id)})
        )
        ordered = tuple(items[entry.id] for entry in entries if entry.id in items)
        return ProcessResult(
            run_id=run_id,
            status=status,
            counts=counts,
            items=ordered,
            issues=tuple(issues),
        )

    def _prepare_and_upload(
        self,
        entries: tuple[RegistryEntry, ...],
        workspace: Path,
    ) -> dict[str, tuple[Any, Any, RemoteAudioObject] | Exception]:
        """Prepare locally while verified remote batches upload in background."""

        outcomes: dict[str, tuple[Any, Any, RemoteAudioObject] | Exception] = {}
        pending: list[tuple[RegistryEntry, Any, Any]] = []
        uploads: list[
            tuple[
                tuple[tuple[RegistryEntry, Any, Any], ...],
                Future[tuple[RemoteAudioObject, ...]],
            ]
        ] = []
        expected_errors = (
            ConfigurationError,
            DependencyError,
            ProcessingError,
            OSError,
        )

        with ThreadPoolExecutor(
            max_workers=self.config.remote_audio.upload_workers,
            thread_name_prefix="registry-audio-upload",
        ) as executor:

            def submit_batch() -> None:
                if not pending:
                    return
                batch = tuple(pending)
                pending.clear()
                requests = tuple(
                    AudioUploadRequest(item.audio_resolved_path, prepared.source_audio_sha256)
                    for item, _transcript, prepared in batch
                )
                uploads.append(
                    (batch, executor.submit(self.audio_object_store.ensure_many, requests))
                )

            for entry in entries:
                try:
                    transcript = normalize_transcript(
                        entry.id,
                        entry.transcript_raw,
                        profile=self.config.text.normalizer,
                        lowercase=self.config.text.lowercase,
                    )
                    prepared = self.artifact_store.prepare(entry, self.config.audio, workspace)
                except Exception as exc:
                    if not isinstance(exc, expected_errors):
                        raise
                    outcomes[entry.id] = exc
                    continue
                pending.append((entry, transcript, prepared))
                if len(pending) >= self.config.remote_audio.batch_size:
                    submit_batch()
            submit_batch()

            for batch, future in uploads:
                try:
                    remote_objects = future.result()
                except Exception as exc:
                    if not isinstance(exc, expected_errors):
                        raise
                    for entry, _transcript, _prepared in batch:
                        outcomes[entry.id] = exc
                    continue
                if len(remote_objects) != len(batch):
                    raise ProcessingError("remote audio batch returned the wrong number of objects")
                for (entry, transcript, prepared), remote_object in zip(
                    batch, remote_objects, strict=True
                ):
                    outcomes[entry.id] = (transcript, prepared, remote_object)
        return outcomes

    def status(self) -> dict[str, Any]:
        with self._uows() as factory, factory() as uow:
            result = uow.repository.latest_status()
        return result or {"status": "empty"}

    def history(self, recording_id: str) -> list[dict[str, Any]]:
        with self._uows() as factory, factory() as uow:
            return uow.repository.recording_history(recording_id)

    def fetch_audio(self, recording_id: str, output: Path | None = None) -> dict[str, Any]:
        with self._uows() as factory, factory() as uow:
            asset = uow.repository.current_audio_asset(recording_id)
        if asset is None:
            raise ConfigurationError(f"recording does not exist: {recording_id}")
        extension = str(asset["original_extension"])
        safe_id = "".join(
            character if character.isalnum() or character in {"-", "_", "."} else "_"
            for character in recording_id
        )
        destination = (output or Path.cwd() / f"{safe_id}{extension}").resolve(strict=False)
        fetched = self.audio_object_store.fetch(
            str(asset["remote_storage_key"]),
            str(asset["sha256"]),
            destination,
        )
        return {
            "recording_id": recording_id,
            "audio_asset_id": asset["id"],
            "sha256": asset["sha256"],
            "remote_storage_key": asset["remote_storage_key"],
            "path": str(fetched),
        }

    def qc(self) -> list[dict[str, Any]]:
        with self._uows() as factory, factory() as uow:
            recordings = uow.repository.effective_recordings()
            segments = uow.repository.effective_segments()
        return check_stored_alignments("current", recordings, segments)

    def cache_status(self) -> dict[str, Any]:
        return {
            "local_working_files": self.artifact_store.status(),
            "remote_audio": self.audio_object_store.status(),
        }

    def export(self, formats: tuple[str, ...], output: Path | None = None) -> list[Path]:
        destination = (output or self.config.output.directory).resolve(strict=False)
        with self._uows() as factory, factory() as uow:
            recordings = uow.repository.effective_recordings()
            segments = uow.repository.effective_segments()
        issues = self.qc()
        paths: list[Path] = []
        if "jsonl" in formats:
            paths.extend(export_jsonl(destination, recordings, segments))
        if "csv" in formats:
            paths.extend(export_csv(destination, recordings, segments, issues))
        if "textgrid" in formats:
            paths.extend(export_textgrids(destination, segments))
        return paths

    def prune_runs(self, *, dry_run: bool) -> list[UUID]:
        retention = self.config.retention
        if not retention.prune_runs:
            return []
        with self._uows() as factory, factory() as uow:
            result = uow.repository.prune_runs(
                now=datetime.now(UTC),
                run_metadata_days=retention.run_metadata_days,
                failed_run_days=retention.failed_run_days,
                minimum_runs_to_keep=retention.minimum_runs_to_keep,
                dry_run=dry_run,
            )
            if not dry_run:
                uow.commit()
        return result
