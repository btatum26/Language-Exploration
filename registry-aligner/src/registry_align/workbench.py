"""Qt-free application services used by interactive alignment clients."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from registry_align.audio.probe import sha256_file
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.segments import SegmentReplacement, SegmentRevision
from registry_align.domain.topology import replay_effective_topology, validate_candidate_revision
from registry_align.errors import ConfigurationError
from registry_align.pipeline.service import AlignmentService


class RegistryWorkbenchService:
    """GUI-oriented facade that keeps SQLAlchemy and storage rules in the backend."""

    def __init__(self, alignment: AlignmentService) -> None:
        self.alignment = alignment

    def check_connection(self) -> dict[str, Any]:
        return self.alignment.db_check()

    def catalog(
        self,
        *,
        text: str = "",
        language: str | None = None,
        review_state: str | None = None,
        speaker_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 200:
            raise ConfigurationError("catalog offset/limit are invalid")
        with self.alignment._uows() as factory, factory() as uow:
            result = uow.repository.catalog_recordings(
                text=text.strip(),
                language=language or None,
                review_state=review_state or None,
                speaker_id=speaker_id or None,
                offset=offset,
                limit=limit,
            )
        for item in result["items"]:
            item["audio_available"] = self._local_audio_path(item) is not None
        return result

    def recording_detail(self, recording_id: str, *, fetch_missing: bool = False) -> dict[str, Any]:
        with self.alignment._uows() as factory, factory() as uow:
            recording = uow.repository.recording_detail(recording_id)
            if recording is None:
                raise ConfigurationError(f"recording does not exist: {recording_id}")
            segments = uow.repository.effective_segments(recording_id)
            versions = uow.repository.recording_history(recording_id)
        path = self._local_audio_path(recording)
        if path is None and fetch_missing:
            extension = str(recording.get("original_extension") or ".wav")
            destination = (
                self.alignment.config.artifacts.root
                / "resolved-audio"
                / f"{recording['audio_sha256']}{extension}"
            )
            path = Path(self.alignment.fetch_audio(recording_id, destination)["path"])
        recording["audio_available"] = path is not None
        return {
            "recording": recording,
            "audio_path": str(path) if path is not None else None,
            "segments": segments,
            "versions": versions,
        }

    def speakers(self, text: str = "") -> list[dict[str, Any]]:
        with self.alignment._uows() as factory, factory() as uow:
            return uow.repository.speakers(text.strip())

    def create_speaker(
        self, speaker_id: str, display_name: str, language: str | None = None
    ) -> dict[str, Any]:
        identifier = speaker_id.strip()
        name = display_name.strip()
        if not identifier or not name:
            raise ConfigurationError("speaker ID and display name are required")
        with self.alignment._uows() as factory, factory() as uow:
            result = uow.repository.create_speaker(identifier, name, language or None)
            uow.commit()
        return result

    def save_revision(
        self,
        *,
        segment_id: str,
        base_run_id: str,
        start_sample: int | None,
        end_sample: int | None,
        label: str | None,
        review_state: str,
        author: str | None = None,
        reason: str | None = None,
        operation: str = "update",
        affected_segment_ids: tuple[str, ...] = (),
        replacement_segments: tuple[dict[str, Any], ...] = (),
        base_topology_version: int | None = None,
    ) -> dict[str, Any]:
        state = {"needs-review": "proposed"}.get(review_state, review_state)
        if state not in {"proposed", "accepted", "rejected"}:
            raise ConfigurationError(f"unsupported revision review state: {review_state}")
        if start_sample is not None and end_sample is not None and end_sample <= start_sample:
            raise ConfigurationError("revision end must be greater than its start")
        if operation not in {"update", "split", "merge"}:
            raise ConfigurationError(f"unsupported revision operation: {operation}")
        operation_value = cast(Literal["update", "split", "merge"], operation)
        affected = affected_segment_ids or (segment_id,)
        replacements = tuple(
            SegmentReplacement.model_validate(item) for item in replacement_segments
        )
        revision_id = str(uuid4())
        with self.alignment._uows() as factory, factory() as uow:
            model_segments, history = uow.repository.alignment_topology(base_run_id, lock=True)
            topology = replay_effective_topology(model_segments, history)
            if base_topology_version is not None and base_topology_version != topology.version:
                raise ConfigurationError(
                    "the alignment topology changed in another editor; reload before saving "
                    f"(expected {base_topology_version}, current {topology.version})"
                )
            candidate = {
                "id": revision_id,
                "segment_id": segment_id,
                "start_sample_override": start_sample,
                "end_sample_override": end_sample,
                "label_override": label,
                "review_state": state,
                "operation": operation_value,
                "effective_target_segment_ids": affected,
                "replacement_segments": [item.model_dump(mode="json") for item in replacements],
            }
            targets = validate_candidate_revision(topology, candidate)
            anchors = {str(topology.by_id()[target]["model_segment_id"]) for target in targets}
            if segment_id not in anchors:
                raise ConfigurationError(
                    "revision history anchor does not own the effective target; reload and retry"
                )
            revision = SegmentRevision(
                revision_id=revision_id,
                segment_id=segment_id,
                base_run_id=base_run_id,
                start_sample_override=start_sample,
                end_sample_override=end_sample,
                label_override=label,
                review_status=state,
                author=author,
                created_at=datetime.now(UTC),
                reason=reason,
                operation=operation_value,
                affected_segment_ids=affected,
                effective_target_segment_ids=targets,
                replacement_segments=replacements,
                base_topology_version=topology.version,
            )
            revision_id = uow.repository.save_revision(revision)
            uow.commit()
        return {
            "revision_id": revision_id,
            "review_state": state,
            "topology_version": topology.version + (state == "accepted"),
        }

    @staticmethod
    def _validate_revision_operation(
        operation: str,
        model_segments: list[dict[str, Any]],
        replacements: tuple[SegmentReplacement, ...],
        *,
        start_sample: int | None,
        end_sample: int | None,
        parents: list[dict[str, Any]],
    ) -> None:
        expected_sources = 1 if operation in {"update", "split"} else 2
        if len(model_segments) != expected_sources:
            raise ConfigurationError(f"{operation} requires {expected_sources} original segment(s)")
        if operation == "update":
            if replacements:
                raise ConfigurationError("update revisions cannot contain replacements")
            source = model_segments[0]
            effective_start = start_sample if start_sample is not None else source["start_sample"]
            effective_end = end_sample if end_sample is not None else source["end_sample"]
            if effective_end <= effective_start:
                raise ConfigurationError("revision end must be greater than its start")
            if parents:
                parent = parents[0]
                if effective_start < parent["start_sample"] or effective_end > parent["end_sample"]:
                    raise ConfigurationError("phone boundaries must remain inside the parent word")
            return
        ordered = sorted(model_segments, key=lambda item: item["start_sample"])
        if operation == "split":
            if len(replacements) != 2:
                raise ConfigurationError("split revisions require two replacements")
            first, second = sorted(replacements, key=lambda item: item.start_sample)
            source = ordered[0]
            parent_id = (
                str(source["parent_segment_id"])
                if source["parent_segment_id"] is not None
                else None
            )
            if (
                first.start_sample != source["start_sample"]
                or first.end_sample != second.start_sample
                or second.end_sample != source["end_sample"]
            ):
                raise ConfigurationError("split replacements must exactly partition the segment")
            if any(item.parent_segment_id != parent_id for item in replacements):
                raise ConfigurationError("split replacements must preserve the parent word")
            return
        left, right = ordered
        if (
            left["kind"] != right["kind"]
            or left["parent_segment_id"] != right["parent_segment_id"]
            or left["end_sample"] != right["start_sample"]
        ):
            raise ConfigurationError("merge segments must be adjacent in the same tier and parent")
        if len(replacements) != 1:
            raise ConfigurationError("merge revisions require one replacement")
        replacement = replacements[0]
        parent_id = (
            str(left["parent_segment_id"]) if left["parent_segment_id"] is not None else None
        )
        if (
            replacement.start_sample != left["start_sample"]
            or replacement.end_sample != right["end_sample"]
            or replacement.parent_segment_id != parent_id
        ):
            raise ConfigurationError("merge replacement must span both source segments")

    def revision_history(self, segment_id: str) -> list[dict[str, Any]]:
        with self.alignment._uows() as factory, factory() as uow:
            return uow.repository.revision_history(segment_id)

    def ingest_local_recording(
        self,
        *,
        recording_id: str,
        audio_path: Path,
        transcript: str,
        language: str,
        speaker_id: str | None,
        metadata: dict[str, Any] | None = None,
        overwrite_existing: bool = False,
        align: bool = True,
    ) -> dict[str, Any]:
        if not align:
            raise ConfigurationError("deferred alignment is not supported by this backend yet")
        source = audio_path.resolve(strict=True)
        if not transcript:
            raise ConfigurationError("the exact transcript is required")
        canonical, logical_path = self._canonicalize_local_audio(source)
        entry = RegistryEntry(
            id=recording_id,
            source_entry_index=0,
            source_registry_path=source,
            audio_relative_path=logical_path,
            audio_resolved_path=canonical,
            transcript_raw=transcript,
            language=language,
            speaker_id=speaker_id,
            metadata=metadata or {},
        )
        result = self.alignment.process_entries((entry,), overwrite_existing=overwrite_existing)
        return result.model_dump(mode="json")

    def _canonicalize_local_audio(self, source: Path) -> tuple[Path, str]:
        digest = sha256_file(source)
        extension = source.suffix.lower() or ".audio"
        relative = Path("workbench-recordings") / digest[:2] / f"{digest}{extension}"
        destination = (self.alignment.config.source.root / relative).resolve(strict=False)
        if not destination.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".tmp-{uuid4().hex[:8]}")
            try:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != digest:
                    raise ConfigurationError("canonical local audio copy failed verification")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return destination, relative.as_posix()

    def _local_audio_path(self, recording: dict[str, Any]) -> Path | None:
        relative = recording.get("audio_relative_path")
        if relative:
            source = (self.alignment.config.source.root / str(relative)).resolve(strict=False)
            if source.is_file():
                return source
        sha256 = recording.get("audio_sha256")
        extension = recording.get("original_extension") or ".wav"
        if sha256:
            cached = (
                self.alignment.config.artifacts.root / "resolved-audio" / f"{sha256}{extension}"
            ).resolve(strict=False)
            if cached.is_file():
                return cached
        return None
