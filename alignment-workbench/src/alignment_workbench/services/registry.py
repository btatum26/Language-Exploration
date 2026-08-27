from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from registry_align.config import AppConfig, load_config
from registry_align.pipeline.service import AlignmentService
from registry_align.storage.database import DatabaseRuntime, check_database
from registry_align.storage.postgres import PostgresUnitOfWorkFactory
from registry_align.storage.schema import schema_status
from registry_align.workbench import RegistryWorkbenchService

from alignment_workbench.services.models import (
    CatalogPage,
    CatalogQuery,
    IngestRequest,
    RecordingDetail,
    RecordingSummary,
    RevisionRequest,
    SegmentData,
)


class RegistryServices:
    """Qt-free GUI facade; SQLAlchemy remains behind registry-aligner's unit of work."""

    def __init__(
        self,
        backend: RegistryWorkbenchService | None = None,
        *,
        config: AppConfig | None = None,
        adapter_mode: bool = True,
        mfa_preflight: dict[str, Any] | None = None,
    ) -> None:
        if backend is None and config is None:
            raise ValueError("a registry backend or configuration is required")
        self._backend = backend
        self._config = config
        self._adapter_mode = adapter_mode
        self._mfa_preflight = mfa_preflight or {"usable": False, "searched": ()}
        self._runtime: DatabaseRuntime | None = None
        self._engine: Any = None
        self._lock = threading.RLock()

    @classmethod
    def from_environment(
        cls,
        config_path: Path | None = None,
        *,
        dotenv_path: Path | None = None,
    ) -> RegistryServices:
        workbench_env = dotenv_path or Path(__file__).resolve().parents[3] / ".env"
        load_dotenv(dotenv_path=workbench_env, override=False)
        config = load_config(config_path)
        if config_path is not None:
            config = cls._resolve_config_paths(config, config_path.parent.resolve())
        registry_root = config_path.parent.resolve() if config_path is not None else Path.cwd()
        config, preflight = cls._discover_mfa(config, registry_root)
        return cls(
            config=config,
            adapter_mode=config_path is not None,
            mfa_preflight=preflight,
        )

    @staticmethod
    def _discover_mfa(config: AppConfig, registry_root: Path) -> tuple[AppConfig, dict[str, Any]]:
        configured = config.alignment.mfa.executable
        environment = os.getenv("REGISTRY_ALIGN_MFA", "").strip()
        searched: list[str] = []
        selected: str | None = None
        if environment:
            searched.append(environment)
            selected = shutil.which(environment) or (
                str(Path(environment).resolve(strict=False))
                if Path(environment).is_file()
                else None
            )
        elif configured and configured != "mfa":
            searched.append(configured)
            selected = shutil.which(configured) or (
                str(Path(configured).resolve(strict=False)) if Path(configured).is_file() else None
            )
        else:
            path_match = shutil.which("mfa")
            searched.append(path_match or "PATH:mfa")
            if path_match:
                selected = path_match
            pixi = (
                registry_root / ".pixi" / "envs" / "default" / "Scripts" / "mfa.exe"
                if os.name == "nt"
                else registry_root / ".pixi" / "envs" / "default" / "bin" / "mfa"
            )
            searched.append(str(pixi))
            if selected is None and pixi.is_file():
                selected = str(pixi)
        preflight = {
            "usable": selected is not None,
            "executable": selected,
            "searched": tuple(searched),
            "message": (
                f"MFA: {selected}"
                if selected is not None
                else "MFA unavailable; searched: " + ", ".join(searched)
            ),
        }
        if selected is None:
            return config, preflight
        mfa = config.alignment.mfa.model_copy(update={"executable": selected})
        return (
            config.model_copy(
                update={"alignment": config.alignment.model_copy(update={"mfa": mfa})}
            ),
            preflight,
        )

    def _service(self) -> RegistryWorkbenchService:
        with self._lock:
            if self._backend is not None:
                return self._backend
            assert self._config is not None
            runtime = DatabaseRuntime(self._config.database)
            try:
                engine = runtime.__enter__()
                alignment = AlignmentService(
                    self._config,
                    uow_factory=PostgresUnitOfWorkFactory(engine),
                    adapter_mode=self._adapter_mode,
                )
                backend = RegistryWorkbenchService(alignment)
            except Exception:
                runtime.__exit__(None, None, None)
                raise
            self._runtime = runtime
            self._engine = engine
            self._backend = backend
            return backend

    def close(self) -> None:
        """Dispose the database engine and stop the workbench-owned SSH tunnel."""
        with self._lock:
            runtime = self._runtime
            self._runtime = None
            self._engine = None
            if self._config is not None:
                self._backend = None
        if runtime is not None:
            runtime.__exit__(None, None, None)

    @staticmethod
    def _resolve_config_paths(config: AppConfig, base: Path) -> AppConfig:
        def local_path(value: Path) -> Path:
            return value if value.is_absolute() else (base / value).resolve(strict=False)

        def local_identity(value: str) -> str:
            path = Path(value)
            looks_like_path = value.startswith(".") or "/" in value or "\\" in value
            return (
                str(local_path(path))
                if value and looks_like_path and not path.is_absolute()
                else value
            )

        mfa = config.alignment.mfa.model_copy(
            update={
                "config_path": local_identity(config.alignment.mfa.config_path),
                "dictionary": local_identity(config.alignment.mfa.dictionary),
                "g2p_model": local_identity(config.alignment.mfa.g2p_model),
            }
        )
        return config.model_copy(
            update={
                "source": config.source.model_copy(update={"root": local_path(config.source.root)}),
                "artifacts": config.artifacts.model_copy(
                    update={"root": local_path(config.artifacts.root)}
                ),
                "output": config.output.model_copy(
                    update={"directory": local_path(config.output.directory)}
                ),
                "alignment": config.alignment.model_copy(update={"mfa": mfa}),
            }
        )

    def check_connection(self) -> dict[str, Any]:
        backend = self._service()
        if self._engine is None:
            result = backend.check_connection()
            result["mfa"] = self._mfa_preflight
            return result
        try:
            result = check_database(self._engine)
            result["revision"] = schema_status(self._engine)
        except Exception:
            self.close()
            raise
        result["usable"] = bool(result.get("usable") and result["revision"].get("current_is_head"))
        result["mfa"] = self._mfa_preflight
        return result

    def preflight(self) -> dict[str, Any]:
        return dict(self._mfa_preflight)

    def catalog(self, query: CatalogQuery) -> CatalogPage:
        raw = self._service().catalog(
            text=query.text,
            language=query.language,
            review_state=query.review_state,
            speaker_id=query.speaker_id,
            offset=query.offset,
            limit=query.limit,
        )
        items = tuple(self._summary(item) for item in raw["items"])
        return CatalogPage(items, query.offset, int(raw["total"]), bool(raw["has_more"]))

    def recording(self, recording_id: str, *, fetch_missing: bool = False) -> RecordingDetail:
        raw = self._service().recording_detail(recording_id, fetch_missing=fetch_missing)
        segments = tuple(self._segment(item) for item in raw["segments"])
        return RecordingDetail(
            summary=self._summary(raw["recording"]),
            audio_path=Path(raw["audio_path"]) if raw.get("audio_path") else None,
            words=tuple(item for item in segments if item.kind == "word"),
            phones=tuple(item for item in segments if item.kind in {"phone", "silence"}),
            versions=tuple(raw.get("versions", ())),
        )

    def speakers(self, text: str = "") -> tuple[dict[str, Any], ...]:
        return tuple(self._service().speakers(text))

    def create_speaker(self, speaker_id: str, display_name: str, language: str) -> dict[str, Any]:
        return self._service().create_speaker(speaker_id, display_name, language)

    def save_revision(self, request: RevisionRequest) -> dict[str, Any]:
        return self._service().save_revision(
            segment_id=request.segment_id,
            base_run_id=request.base_run_id,
            start_sample=request.start_sample,
            end_sample=request.end_sample,
            label=request.label,
            review_state=request.review_state,
            author=request.author,
            reason=request.reason,
            operation=request.operation,
            affected_segment_ids=request.affected_segment_ids,
            replacement_segments=request.replacement_segments,
            base_topology_version=request.base_topology_version,
        )

    def revision_history(self, segment_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._service().revision_history(segment_id))

    def ingest(self, request: IngestRequest, *, align: bool = True) -> dict[str, Any]:
        return self._service().ingest_local_recording(
            recording_id=request.recording_id,
            audio_path=request.audio_path,
            transcript=request.transcript,
            language=request.language,
            speaker_id=request.speaker_id,
            metadata=request.metadata,
            overwrite_existing=request.overwrite_existing,
            align=align,
        )

    @staticmethod
    def _summary(item: dict[str, Any]) -> RecordingSummary:
        return RecordingSummary(
            recording_id=str(item["recording_id"]),
            recording_version_id=str(item["recording_version_id"]),
            speaker_id=item.get("speaker_id"),
            transcript=str(item.get("transcript_raw", "")),
            language=str(item.get("language", "")),
            recording_created_at=item.get("recording_created_at"),
            version_created_at=item.get("version_created_at") or item.get("created_at"),
            alignment_state=str(item.get("alignment_state", "unaligned")),
            review_state=str(item.get("review_state", "unreviewed")),
            audio_available=bool(item.get("audio_available", False)),
            audio_relative_path=item.get("audio_relative_path"),
            duration_seconds=item.get("duration_seconds"),
        )

    @staticmethod
    def _segment(item: dict[str, Any]) -> SegmentData:
        provenance = dict(item.get("model_provenance", {}))
        if item.get("token_mappings"):
            provenance.setdefault("source_token_ids", tuple(item["token_mappings"]))
        if item.get("alignment_result_id") is not None:
            provenance.setdefault("alignment_result_id", str(item["alignment_result_id"]))
            provenance.setdefault("run_id", str(item["alignment_result_id"]))
        return SegmentData(
            id=str(item["id"]),
            kind=str(item.get("tier", item.get("kind", "phone"))),
            label=str(item["label"]),
            start_sample=int(item["start_sample"]),
            end_sample=int(item["end_sample"]),
            timebase_sample_rate_hz=int(item["timebase_sample_rate_hz"]),
            parent_id=str(item["parent_segment_id"]) if item.get("parent_segment_id") else None,
            confidence=item.get("confidence"),
            review_state=str(item.get("review_state", "automatic")),
            model_label=item.get("model_label"),
            model_start_sample=item.get("model_start_sample"),
            model_end_sample=item.get("model_end_sample"),
            effective_revision_id=(
                str(item["effective_revision_id"]) if item.get("effective_revision_id") else None
            ),
            model_segment_id=item.get("model_segment_id"),
            topology_version=int(item.get("topology_version", 0)),
            provenance=provenance,
        )
