"""Explicit PostgreSQL repository and unit-of-work implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    Connection,
    Engine,
    and_,
    case,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.segments import AlignmentSegment, SegmentRevision
from registry_align.domain.transcripts import NormalizedTranscript
from registry_align.events import Issue
from registry_align.storage.repository import AlignmentClaim, UnitOfWork, VersionIngestion
from registry_align.storage.tables import (
    alignment_results,
    audio_assets,
    issues,
    recording_versions,
    recordings,
    run_items,
    runs,
    segment_revisions,
    segments,
    speakers,
    transcript_versions,
)


class PostgresRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def existing_recording_ids(self, recording_ids: tuple[str, ...]) -> set[str]:
        if not recording_ids:
            return set()
        rows = self.connection.execute(
            select(recordings.c.id).where(recordings.c.id.in_(recording_ids))
        )
        return {str(row.id) for row in rows}

    def start_run(
        self,
        configuration_fingerprint: str,
        backend_summary: dict[str, Any],
        invoking_host: str | None,
    ) -> UUID:
        run_id = uuid4()
        self.connection.execute(
            insert(runs).values(
                id=run_id,
                status="running",
                configuration_fingerprint=configuration_fingerprint,
                backend_summary=backend_summary,
                invoking_host=invoking_host,
            )
        )
        return run_id

    def finish_run(
        self,
        run_id: UUID,
        status: str,
        counts: dict[str, int],
        errors: list[dict[str, Any]],
    ) -> None:
        self.connection.execute(
            update(runs)
            .where(runs.c.id == run_id)
            .values(
                status=status,
                finished_at=datetime.now(UTC),
                aggregate_counts=counts,
                error_summary=errors,
            )
        )

    def save_run_item(
        self,
        run_id: UUID,
        recording_id: str,
        outcome: str,
        *,
        version_id: UUID | None = None,
        alignment_id: UUID | None = None,
        detail: str | None = None,
    ) -> None:
        values = {
            "run_id": run_id,
            "recording_id": recording_id,
            "recording_version_id": version_id,
            "alignment_result_id": alignment_id,
            "outcome": outcome,
            "detail": detail,
        }
        self.connection.execute(
            pg_insert(run_items)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_run_item_recording",
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"run_id", "recording_id"}
                },
            )
        )

    def save_issue(self, run_id: UUID, issue: Issue) -> None:
        self.connection.execute(
            insert(issues).values(
                run_id=run_id,
                recording_id=issue.recording_id,
                code=issue.code,
                severity=issue.severity.value,
                stage=issue.stage,
                message=issue.message,
                details=issue.details,
            )
        )

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
    ) -> VersionIngestion:
        self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": entry.id}
        )
        current_before = self.connection.execute(
            select(recordings.c.current_version_id)
            .where(recordings.c.id == entry.id)
            .with_for_update()
        ).scalar_one_or_none()
        if current_before is not None and not overwrite_existing:
            return VersionIngestion(recording_version_id=current_before, state="skipped")
        self.connection.execute(
            pg_insert(recordings)
            .values(
                id=entry.id,
                corpus_id=corpus_id,
                speaker_id=entry.speaker_id,
                language=entry.language,
            )
            .on_conflict_do_nothing(index_elements=[recordings.c.id])
        )
        audio_id = uuid4()
        inserted_audio = self.connection.execute(
            pg_insert(audio_assets)
            .values(
                id=audio_id,
                sha256=prepared.source_audio_sha256,
                original_extension=entry.audio_resolved_path.suffix.lower(),
                codec=prepared.source_codec,
                sample_rate_hz=prepared.source_sample_rate_hz,
                channels=prepared.source_channels,
                duration_seconds=prepared.source_duration_s,
                frame_count=prepared.source_frame_count,
                logical_path=entry.audio_relative_path,
                remote_storage_key=remote_storage_key,
                source_metadata={},
            )
            .on_conflict_do_nothing(index_elements=[audio_assets.c.sha256])
            .returning(audio_assets.c.id)
        ).scalar_one_or_none()
        if inserted_audio is None:
            audio_id = self.connection.execute(
                select(audio_assets.c.id).where(
                    audio_assets.c.sha256 == prepared.source_audio_sha256
                )
            ).scalar_one()

        transcript_id = uuid4()
        tokenization = {
            "source_tokens": [item.model_dump(mode="json") for item in transcript.source_tokens],
            "alignment_tokens": [
                item.model_dump(mode="json") for item in transcript.alignment_tokens
            ],
            "mappings": [item.model_dump(mode="json") for item in transcript.token_mappings],
        }
        inserted_transcript = self.connection.execute(
            pg_insert(transcript_versions)
            .values(
                id=transcript_id,
                raw_text=transcript.raw_text,
                normalized_text=transcript.normalized_text,
                raw_sha256=transcript.raw_sha256,
                normalized_sha256=transcript.normalized_sha256,
                normalizer_name=transcript.normalizer_name,
                normalizer_version=transcript.normalizer_version,
                tokenization=tokenization,
                warnings=list(transcript.warnings),
            )
            .on_conflict_do_nothing(constraint="uq_transcript_content")
            .returning(transcript_versions.c.id)
        ).scalar_one_or_none()
        if inserted_transcript is None:
            transcript_id = self.connection.execute(
                select(transcript_versions.c.id).where(
                    and_(
                        transcript_versions.c.raw_sha256 == transcript.raw_sha256,
                        transcript_versions.c.normalized_sha256 == transcript.normalized_sha256,
                        transcript_versions.c.normalizer_name == transcript.normalizer_name,
                        transcript_versions.c.normalizer_version == transcript.normalizer_version,
                    )
                )
            ).scalar_one()

        current_id = self.connection.execute(
            select(recordings.c.current_version_id)
            .where(recordings.c.id == entry.id)
            .with_for_update()
        ).scalar_one()
        version_id = uuid4()
        inserted_version = self.connection.execute(
            pg_insert(recording_versions)
            .values(
                id=version_id,
                recording_id=entry.id,
                audio_asset_id=audio_id,
                transcript_version_id=transcript_id,
                language=entry.language,
                speaker_id=entry.speaker_id,
                user_metadata=entry.metadata,
                content_fingerprint=content_fingerprint,
                superseded_version_id=current_id,
                ingestion_reason=ingestion_reason,
            )
            .on_conflict_do_nothing(constraint="uq_recording_content_state")
            .returning(recording_versions.c.id)
        ).scalar_one_or_none()
        if inserted_version is None:
            version_id = self.connection.execute(
                select(recording_versions.c.id).where(
                    and_(
                        recording_versions.c.recording_id == entry.id,
                        recording_versions.c.content_fingerprint == content_fingerprint,
                    )
                )
            ).scalar_one()
            return VersionIngestion(recording_version_id=version_id, state="unchanged")
        self.connection.execute(
            update(recordings)
            .where(recordings.c.id == entry.id)
            .values(
                current_version_id=version_id,
                speaker_id=entry.speaker_id,
                language=entry.language,
            )
        )
        return VersionIngestion(recording_version_id=version_id, state="created")

    def claim_alignment(
        self,
        version_id: UUID,
        processing_fingerprint: str,
        provenance: dict[str, str],
        *,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AlignmentClaim:
        alignment_id = uuid4()
        now = datetime.now(UTC)
        inserted = self.connection.execute(
            pg_insert(alignment_results)
            .values(
                id=alignment_id,
                recording_version_id=version_id,
                processing_fingerprint=processing_fingerprint,
                status="running",
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                **provenance,
            )
            .on_conflict_do_nothing(constraint="uq_alignment_processing")
            .returning(alignment_results.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return AlignmentClaim(alignment_result_id=alignment_id, state="claimed")
        row = self.connection.execute(
            select(
                alignment_results.c.id,
                alignment_results.c.status,
                alignment_results.c.lease_expires_at,
            )
            .where(
                and_(
                    alignment_results.c.recording_version_id == version_id,
                    alignment_results.c.processing_fingerprint == processing_fingerprint,
                )
            )
            .with_for_update()
        ).one()
        if row.status == "complete":
            return AlignmentClaim(alignment_result_id=row.id, state="complete")
        if row.status == "running" and row.lease_expires_at and row.lease_expires_at > now:
            return AlignmentClaim(alignment_result_id=row.id, state="busy")
        self.connection.execute(
            update(alignment_results)
            .where(alignment_results.c.id == row.id)
            .values(
                status="running",
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                qc_summary={},
            )
        )
        return AlignmentClaim(alignment_result_id=row.id, state="claimed")

    def complete_alignment(
        self,
        alignment_id: UUID,
        model_segments: tuple[AlignmentSegment, ...],
        qc_summary: dict[str, Any],
    ) -> None:
        ids = {item.segment_id: uuid4() for item in model_segments}
        values: list[dict[str, Any]] = []
        ordinal_by_kind: dict[str, int] = {}
        for item in model_segments:
            ordinal = ordinal_by_kind.get(item.tier, 0)
            ordinal_by_kind[item.tier] = ordinal + 1
            values.append(
                {
                    "id": ids[item.segment_id],
                    "alignment_result_id": alignment_id,
                    "kind": item.tier,
                    "ordinal": ordinal,
                    "label": item.label,
                    "backend_label": item.backend_label,
                    "start_sample": item.start_sample,
                    "end_sample": item.end_sample,
                    "timebase_sample_rate_hz": item.timebase_sample_rate_hz,
                    "parent_segment_id": (
                        ids.get(item.parent_segment_id) if item.parent_segment_id else None
                    ),
                    "token_mappings": list(item.source_token_ids),
                    "confidence": item.confidence,
                    "review_state": item.review_status,
                    "model_provenance": {
                        "source": item.source,
                        "model_id": item.model_id,
                        "backend_segment_id": item.segment_id,
                        "notes": item.notes,
                    },
                }
            )
        parentless = [value for value in values if value["parent_segment_id"] is None]
        children = [value for value in values if value["parent_segment_id"] is not None]
        if parentless:
            self.connection.execute(insert(segments), parentless)
        if children:
            self.connection.execute(insert(segments), children)
        self.connection.execute(
            update(alignment_results)
            .where(
                and_(
                    alignment_results.c.id == alignment_id,
                    alignment_results.c.status == "running",
                )
            )
            .values(
                status="complete",
                qc_summary=qc_summary,
                completed_at=datetime.now(UTC),
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        version_id = select(alignment_results.c.recording_version_id).where(
            alignment_results.c.id == alignment_id
        )
        self.connection.execute(
            update(recording_versions)
            .where(recording_versions.c.id == version_id.scalar_subquery())
            .values(current_alignment_result_id=alignment_id)
        )

    def fail_alignment(self, alignment_id: UUID, summary: dict[str, Any]) -> None:
        self.connection.execute(
            update(alignment_results)
            .where(
                and_(
                    alignment_results.c.id == alignment_id,
                    alignment_results.c.status == "running",
                )
            )
            .values(
                status="failed",
                qc_summary=summary,
                completed_at=datetime.now(UTC),
                lease_owner=None,
                lease_expires_at=None,
            )
        )

    def latest_status(self) -> dict[str, Any] | None:
        row = (
            self.connection.execute(select(runs).order_by(runs.c.started_at.desc()).limit(1))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def catalog_recordings(
        self,
        *,
        text: str,
        language: str | None,
        review_state: str | None,
        speaker_id: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        accepted = exists(
            select(segment_revisions.c.id)
            .select_from(segment_revisions)
            .join(segments, segments.c.id == segment_revisions.c.segment_id)
            .where(
                segments.c.alignment_result_id == recording_versions.c.current_alignment_result_id,
                segment_revisions.c.review_state == "accepted",
            )
        )
        rejected = exists(
            select(segment_revisions.c.id)
            .select_from(segment_revisions)
            .join(segments, segments.c.id == segment_revisions.c.segment_id)
            .where(
                segments.c.alignment_result_id == recording_versions.c.current_alignment_result_id,
                segment_revisions.c.review_state == "rejected",
            )
        )
        proposed = exists(
            select(segment_revisions.c.id)
            .select_from(segment_revisions)
            .join(segments, segments.c.id == segment_revisions.c.segment_id)
            .where(
                segments.c.alignment_result_id == recording_versions.c.current_alignment_result_id,
                segment_revisions.c.review_state == "proposed",
            )
        )
        review_expression = case(
            (proposed, "needs-review"),
            (rejected, "rejected"),
            (accepted, "verified"),
            else_="unreviewed",
        )
        alignment_expression = func.coalesce(alignment_results.c.status, "unaligned")
        statement = (
            select(
                recordings.c.id.label("recording_id"),
                recordings.c.created_at.label("recording_created_at"),
                recording_versions.c.id.label("recording_version_id"),
                recording_versions.c.created_at.label("version_created_at"),
                recording_versions.c.language,
                recording_versions.c.speaker_id,
                transcript_versions.c.raw_text.label("transcript_raw"),
                audio_assets.c.logical_path.label("audio_relative_path"),
                audio_assets.c.duration_seconds,
                audio_assets.c.sha256.label("audio_sha256"),
                audio_assets.c.original_extension,
                alignment_expression.label("alignment_state"),
                review_expression.label("review_state"),
            )
            .select_from(recordings)
            .join(recording_versions, recording_versions.c.id == recordings.c.current_version_id)
            .join(audio_assets, audio_assets.c.id == recording_versions.c.audio_asset_id)
            .join(
                transcript_versions,
                transcript_versions.c.id == recording_versions.c.transcript_version_id,
            )
            .outerjoin(
                alignment_results,
                alignment_results.c.id == recording_versions.c.current_alignment_result_id,
            )
        )
        conditions = []
        if language:
            conditions.append(recording_versions.c.language == language)
        if speaker_id:
            conditions.append(recording_versions.c.speaker_id == speaker_id)
        if review_state:
            conditions.append(review_expression == review_state)
        if text:
            pattern = f"%{text}%"
            matching_segment = exists(
                select(segments.c.id).where(
                    segments.c.alignment_result_id
                    == recording_versions.c.current_alignment_result_id,
                    segments.c.label.ilike(pattern),
                )
            )
            conditions.append(
                or_(
                    recordings.c.id.ilike(pattern),
                    recording_versions.c.speaker_id.ilike(pattern),
                    transcript_versions.c.raw_text.ilike(pattern),
                    matching_segment,
                )
            )
        if conditions:
            statement = statement.where(*conditions)
        total = self.connection.execute(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ).scalar_one()
        rows = self.connection.execute(
            statement.order_by(recording_versions.c.created_at.desc()).offset(offset).limit(limit)
        ).mappings()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total),
            "has_more": offset + limit < int(total),
        }

    def recording_detail(self, recording_id: str) -> dict[str, Any] | None:
        page = self.catalog_recordings(
            text=recording_id,
            language=None,
            review_state=None,
            speaker_id=None,
            offset=0,
            limit=100,
        )
        return next((item for item in page["items"] if item["recording_id"] == recording_id), None)

    def speakers(self, text: str = "") -> list[dict[str, Any]]:
        statement = select(speakers).order_by(speakers.c.display_name, speakers.c.id)
        if text:
            pattern = f"%{text}%"
            statement = statement.where(
                or_(speakers.c.id.ilike(pattern), speakers.c.display_name.ilike(pattern))
            )
        return [dict(row) for row in self.connection.execute(statement.limit(100)).mappings()]

    def create_speaker(
        self, speaker_id: str, display_name: str, language: str | None
    ) -> dict[str, Any]:
        self.connection.execute(
            pg_insert(speakers)
            .values(id=speaker_id, display_name=display_name, language=language)
            .on_conflict_do_update(
                index_elements=[speakers.c.id],
                set_={"display_name": display_name, "language": language},
            )
        )
        row = self.connection.execute(select(speakers).where(speakers.c.id == speaker_id))
        return dict(row.mappings().one())

    def recording_history(self, recording_id: str) -> list[dict[str, Any]]:
        statement = (
            select(
                recording_versions.c.id,
                recording_versions.c.created_at,
                recording_versions.c.ingestion_reason,
                recording_versions.c.content_fingerprint,
                recording_versions.c.language,
                recording_versions.c.speaker_id,
                audio_assets.c.sha256.label("audio_sha256"),
                audio_assets.c.remote_storage_key,
                transcript_versions.c.raw_sha256.label("transcript_sha256"),
                (recordings.c.current_version_id == recording_versions.c.id).label("is_current"),
            )
            .join(recordings, recordings.c.id == recording_versions.c.recording_id)
            .join(audio_assets, audio_assets.c.id == recording_versions.c.audio_asset_id)
            .join(
                transcript_versions,
                transcript_versions.c.id == recording_versions.c.transcript_version_id,
            )
            .where(recording_versions.c.recording_id == recording_id)
            .order_by(recording_versions.c.created_at.desc())
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def current_audio_asset(self, recording_id: str) -> dict[str, Any] | None:
        statement = (
            select(
                audio_assets.c.id,
                audio_assets.c.sha256,
                audio_assets.c.original_extension,
                audio_assets.c.remote_storage_key,
                audio_assets.c.codec,
                audio_assets.c.duration_seconds,
            )
            .select_from(recordings)
            .join(
                recording_versions,
                recording_versions.c.id == recordings.c.current_version_id,
            )
            .join(audio_assets, audio_assets.c.id == recording_versions.c.audio_asset_id)
            .where(recordings.c.id == recording_id)
        )
        row = self.connection.execute(statement).mappings().one_or_none()
        return dict(row) if row is not None else None

    def effective_recordings(self) -> list[dict[str, Any]]:
        statement = (
            select(
                recordings.c.id.label("recording_id"),
                recording_versions.c.id.label("recording_version_id"),
                recording_versions.c.created_at,
                recording_versions.c.language,
                recording_versions.c.speaker_id,
                recording_versions.c.user_metadata,
                audio_assets.c.sha256.label("source_audio_sha256"),
                audio_assets.c.logical_path.label("audio_relative_path"),
                audio_assets.c.remote_storage_key,
                audio_assets.c.frame_count.label("canonical_frame_count"),
                audio_assets.c.sample_rate_hz.label("canonical_sample_rate_hz"),
                transcript_versions.c.raw_text.label("transcript_raw"),
                transcript_versions.c.normalized_text,
            )
            .join(recording_versions, recordings.c.current_version_id == recording_versions.c.id)
            .join(audio_assets, audio_assets.c.id == recording_versions.c.audio_asset_id)
            .join(
                transcript_versions,
                transcript_versions.c.id == recording_versions.c.transcript_version_id,
            )
            .order_by(recordings.c.id)
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def effective_segments(self, recording_id: str | None = None) -> list[dict[str, Any]]:
        latest_revision = (
            select(segment_revisions.c.id)
            .where(
                and_(
                    segment_revisions.c.segment_id == segments.c.id,
                    segment_revisions.c.review_state == "accepted",
                )
            )
            .correlate(segments)
            .order_by(segment_revisions.c.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            select(
                segments,
                recordings.c.id.label("recording_id"),
                segment_revisions.c.id.label("effective_revision_id"),
                segment_revisions.c.start_sample_override,
                segment_revisions.c.end_sample_override,
                segment_revisions.c.label_override,
                segment_revisions.c.review_state.label("effective_review_state"),
                segment_revisions.c.operation,
                segment_revisions.c.affected_segment_ids,
                segment_revisions.c.replacement_segments,
            )
            .join(alignment_results, alignment_results.c.id == segments.c.alignment_result_id)
            .join(
                recording_versions,
                and_(
                    recording_versions.c.id == alignment_results.c.recording_version_id,
                    recording_versions.c.current_alignment_result_id == alignment_results.c.id,
                ),
            )
            .join(
                recordings,
                recordings.c.current_version_id == recording_versions.c.id,
            )
            .outerjoin(segment_revisions, segment_revisions.c.id == latest_revision)
            .where(alignment_results.c.status == "complete")
            .order_by(recordings.c.id, segments.c.start_sample, segments.c.kind)
        )
        if recording_id is not None:
            statement = statement.where(recordings.c.id == recording_id)
        sources = [dict(source) for source in self.connection.execute(statement).mappings()]
        absorbed: set[str] = set()
        for row in sources:
            if row.get("operation") != "merge":
                continue
            base_id = str(row["id"])
            absorbed.update(
                str(item) for item in row.get("affected_segment_ids") or [] if str(item) != base_id
            )

        output: list[dict[str, Any]] = []
        for row in sources:
            if str(row["id"]) in absorbed:
                continue
            row["model_segment_id"] = str(row["id"])
            row["model_start_sample"] = row["start_sample"]
            row["model_end_sample"] = row["end_sample"]
            row["model_label"] = row["label"]
            if row.get("effective_review_state"):
                row["review_state"] = row["effective_review_state"]
            replacements = row.get("replacement_segments") or []
            if row.get("operation") in {"split", "merge"} and replacements:
                for replacement in replacements:
                    effective = dict(row)
                    effective["id"] = replacement["segment_id"]
                    effective["label"] = replacement["label"]
                    effective["start_sample"] = replacement["start_sample"]
                    effective["end_sample"] = replacement["end_sample"]
                    effective["parent_segment_id"] = replacement.get("parent_segment_id")
                    effective["start_s"] = (
                        effective["start_sample"] / effective["timebase_sample_rate_hz"]
                    )
                    effective["end_s"] = (
                        effective["end_sample"] / effective["timebase_sample_rate_hz"]
                    )
                    effective["tier"] = effective.pop("kind")
                    output.append(effective)
                continue
            if row["start_sample_override"] is not None:
                row["start_sample"] = row["start_sample_override"]
            if row["end_sample_override"] is not None:
                row["end_sample"] = row["end_sample_override"]
            if row["label_override"] is not None:
                row["label"] = row["label_override"]
            row["start_s"] = row["start_sample"] / row["timebase_sample_rate_hz"]
            row["end_s"] = row["end_sample"] / row["timebase_sample_rate_hz"]
            row["tier"] = row.pop("kind")
            output.append(row)
        return sorted(
            output,
            key=lambda item: (item["recording_id"], item["start_sample"], item["tier"]),
        )

    def revision_history(self, segment_id: str) -> list[dict[str, Any]]:
        statement = (
            select(segment_revisions)
            .where(segment_revisions.c.segment_id == UUID(segment_id))
            .order_by(segment_revisions.c.created_at.desc())
        )
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def segments_by_ids(self, segment_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        if not segment_ids:
            return []
        try:
            identifiers = tuple(UUID(item) for item in segment_ids)
        except ValueError as exc:
            raise ValueError("segment revision targets must be original segment UUIDs") from exc
        statement = select(segments).where(segments.c.id.in_(identifiers))
        return [dict(row) for row in self.connection.execute(statement).mappings()]

    def save_revision(self, revision: SegmentRevision) -> UUID:
        revision_id = UUID(revision.revision_id) if revision.revision_id else uuid4()
        self.connection.execute(
            insert(segment_revisions).values(
                id=revision_id,
                segment_id=UUID(revision.segment_id),
                base_run_id=revision.base_run_id,
                start_sample_override=revision.start_sample_override,
                end_sample_override=revision.end_sample_override,
                label_override=revision.label_override,
                review_state=revision.review_status,
                author=revision.author,
                reason=revision.reason,
                operation=revision.operation,
                affected_segment_ids=list(revision.affected_segment_ids),
                replacement_segments=[
                    item.model_dump(mode="json") for item in revision.replacement_segments
                ],
                created_at=revision.created_at,
            )
        )
        return revision_id

    def prune_runs(
        self,
        *,
        now: datetime,
        run_metadata_days: int,
        failed_run_days: int,
        minimum_runs_to_keep: int,
        dry_run: bool,
    ) -> list[UUID]:
        rows = self.connection.execute(
            select(runs.c.id, runs.c.status, runs.c.started_at)
            .where(runs.c.status != "running")
            .order_by(runs.c.started_at.desc())
        ).all()
        candidates: list[UUID] = []
        for index, row in enumerate(rows):
            if index < minimum_runs_to_keep:
                continue
            days = failed_run_days if row.status == "failed" else run_metadata_days
            if row.started_at < now - timedelta(days=days):
                candidates.append(row.id)
        if candidates and not dry_run:
            self.connection.execute(delete(runs).where(runs.c.id.in_(candidates)))
        return candidates


class PostgresUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.connection: Connection | None = None
        self.transaction: Any = None
        self.repository: PostgresRepository

    def __enter__(self) -> PostgresUnitOfWork:
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.repository = PostgresRepository(self.connection)
        return self

    def commit(self) -> None:
        self.transaction.commit()

    def rollback(self) -> None:
        if self.transaction.is_active:
            self.transaction.rollback()

    def __exit__(self, *args: object) -> None:
        self.rollback()
        if self.connection is not None:
            self.connection.close()


class PostgresUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def __call__(self) -> UnitOfWork:
        return cast(UnitOfWork, PostgresUnitOfWork(self.engine))
