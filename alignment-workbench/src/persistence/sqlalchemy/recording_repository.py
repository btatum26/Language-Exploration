"""Immutable recording creation, reads, history, and optimistic saves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from application.errors import (
    ConcurrentRevisionError,
    LibraryVersionNotFoundError,
    PersistenceIntegrityError,
    RecordingNotFoundError,
)
from application.read_models import RecordingRevisionSummary, RecordingSummary, RecordingWorkspace
from models import (
    AnnotatedRecordingSnapshot,
    CreateRecordingRequest,
    PinnedLibraryVersion,
    RevisionMetadata,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
)
from persistence.sqlalchemy.audio_asset_repository import AudioAssetRepository
from persistence.sqlalchemy.mappers import (
    annotation_row_values,
    audio_asset_from_row,
    revision_metadata_from_row,
)
from persistence.sqlalchemy.recording_queries import load_snapshot, load_workspace
from persistence.sqlalchemy.rows import (
    AnnotationLibraryRow,
    AnnotationRow,
    AudioAssetRow,
    LibraryEntryRow,
    LibraryVersionRow,
    RecordingRevisionLibraryRow,
    RecordingRevisionRow,
    RecordingRow,
)


@dataclass(frozen=True, slots=True)
class _ResolvedVersion:
    library_id: UUID
    version_id: UUID
    namespace: str
    version_label: str
    content_sha256: str


class RecordingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, *, limit: int, offset: int) -> tuple[RecordingSummary, ...]:
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset must be nonnegative")
        head = aliased(RecordingRevisionRow)
        rows = self._session.execute(
            select(RecordingRow, head, AudioAssetRow)
            .join(AudioAssetRow, AudioAssetRow.id == RecordingRow.audio_asset_id)
            .join(head, head.id == RecordingRow.head_revision_id)
            .order_by(head.name, RecordingRow.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(
            RecordingSummary(
                recording_id=recording.id,
                head_revision_id=revision.id,
                name=revision.name,
                language=revision.language,
                audio_storage_uri=audio.storage_uri,
                duration_seconds=audio.frame_count / audio.sample_rate_hz,
                revision_number=revision.revision_number,
                revised_at=revision.created_at,
            )
            for recording, revision, audio in rows
        )

    def load_snapshot(
        self, recording_id: UUID, revision_id: UUID | None
    ) -> AnnotatedRecordingSnapshot:
        return load_snapshot(self._session, recording_id, revision_id)

    def load_workspace(self, recording_id: UUID, revision_id: UUID | None) -> RecordingWorkspace:
        return load_workspace(self._session, recording_id, revision_id)

    def list_revisions(self, recording_id: UUID) -> tuple[RecordingRevisionSummary, ...]:
        annotation_counts = (
            select(
                AnnotationRow.recording_revision_id.label("revision_id"),
                func.count().label("annotation_count"),
            )
            .group_by(AnnotationRow.recording_revision_id)
            .subquery()
        )
        rows = tuple(
            self._session.execute(
                select(
                    RecordingRevisionRow,
                    func.coalesce(annotation_counts.c.annotation_count, 0),
                )
                .outerjoin(
                    annotation_counts,
                    annotation_counts.c.revision_id == RecordingRevisionRow.id,
                )
                .where(RecordingRevisionRow.recording_id == recording_id)
                .order_by(RecordingRevisionRow.revision_number)
            )
        )
        if not rows:
            exists = self._session.scalar(
                select(RecordingRow.id).where(RecordingRow.id == recording_id)
            )
            if exists is None:
                raise RecordingNotFoundError(f"recording {recording_id} was not found")
        return tuple(
            RecordingRevisionSummary(
                metadata=revision_metadata_from_row(revision),
                annotation_count=int(annotation_count),
            )
            for revision, annotation_count in rows
        )

    def create(self, request: CreateRecordingRequest) -> AnnotatedRecordingSnapshot:
        if self._session.get(RecordingRow, request.recording_id) is not None:
            raise PersistenceIntegrityError(f"recording {request.recording_id} already exists")
        audio_asset = AudioAssetRepository(self._session).register(request.audio_asset)
        self._session.add(
            RecordingRow(
                id=request.recording_id,
                audio_asset_id=audio_asset.id,
                head_revision_id=None,
            )
        )
        self._session.flush()

        versions = self._resolve_versions(request.libraries)
        entries = self._resolve_entries(request.annotations, versions)
        revision_id = uuid4()
        created_at = datetime.now(UTC)
        snapshot = AnnotatedRecordingSnapshot(
            recording_id=request.recording_id,
            revision=RevisionMetadata(
                id=revision_id,
                number=1,
                parent_id=None,
                created_at=created_at,
                author=request.author,
                message=request.message,
            ),
            name=request.name,
            default_speaker_ref=request.default_speaker_ref,
            language=request.language,
            audio_asset=audio_asset,
            libraries=request.libraries,
            annotations=request.annotations,
        )
        self._insert_revision(snapshot, versions, entries)
        advanced = self._session.scalar(
            update(RecordingRow)
            .where(
                RecordingRow.id == request.recording_id,
                RecordingRow.head_revision_id.is_(None),
            )
            .values(head_revision_id=revision_id)
            .returning(RecordingRow.id)
        )
        if advanced is None:
            raise ConcurrentRevisionError(
                f"recording {request.recording_id} acquired a head during creation"
            )
        return load_snapshot(self._session, request.recording_id, revision_id)

    def save(self, request: SaveRecordingSnapshotRequest) -> AnnotatedRecordingSnapshot:
        state = self._session.execute(
            select(RecordingRow, AudioAssetRow, RecordingRevisionRow)
            .join(AudioAssetRow, AudioAssetRow.id == RecordingRow.audio_asset_id)
            .outerjoin(
                RecordingRevisionRow,
                RecordingRevisionRow.id == RecordingRow.head_revision_id,
            )
            .where(RecordingRow.id == request.recording_id)
        ).one_or_none()
        if state is None:
            raise RecordingNotFoundError(f"recording {request.recording_id} was not found")
        recording, audio_row, parent = state
        if recording.head_revision_id != request.expected_parent_revision_id:
            raise ConcurrentRevisionError(
                f"recording {request.recording_id} no longer has the expected head"
            )

        versions = self._resolve_versions(request.libraries)
        entries = self._resolve_entries(request.annotations, versions)
        revision_id = uuid4()
        revision_number = 1 if parent is None else parent.revision_number + 1
        created_at = datetime.now(UTC)
        snapshot = AnnotatedRecordingSnapshot(
            recording_id=request.recording_id,
            revision=RevisionMetadata(
                id=revision_id,
                number=revision_number,
                parent_id=request.expected_parent_revision_id,
                created_at=created_at,
                author=request.author,
                message=request.message,
            ),
            name=request.name,
            default_speaker_ref=request.default_speaker_ref,
            language=request.language,
            audio_asset=audio_asset_from_row(audio_row),
            libraries=request.libraries,
            annotations=request.annotations,
        )
        try:
            self._insert_revision(snapshot, versions, entries)
        except IntegrityError as exc:
            if request.expected_parent_revision_id is not None and _is_revision_race(exc):
                raise ConcurrentRevisionError(
                    f"recording {request.recording_id} was saved concurrently"
                ) from exc
            raise

        advanced = self._session.scalar(
            update(RecordingRow)
            .where(
                RecordingRow.id == request.recording_id,
                RecordingRow.head_revision_id.is_not_distinct_from(
                    request.expected_parent_revision_id
                ),
            )
            .values(head_revision_id=revision_id)
            .returning(RecordingRow.id)
        )
        if advanced is None:
            raise ConcurrentRevisionError(
                f"recording {request.recording_id} was saved concurrently"
            )
        return load_snapshot(self._session, request.recording_id, revision_id)

    def _resolve_versions(
        self, pins: tuple[PinnedLibraryVersion, ...]
    ) -> dict[tuple[str, str], _ResolvedVersion]:
        if not pins:
            return {}
        namespaces = {pin.namespace for pin in pins}
        labels = {pin.version for pin in pins}
        rows = self._session.execute(
            select(AnnotationLibraryRow, LibraryVersionRow)
            .join(LibraryVersionRow, LibraryVersionRow.library_id == AnnotationLibraryRow.id)
            .where(
                AnnotationLibraryRow.namespace.in_(namespaces),
                LibraryVersionRow.version_label.in_(labels),
            )
        )
        resolved = {
            (library.namespace, version.version_label): _ResolvedVersion(
                library_id=library.id,
                version_id=version.id,
                namespace=library.namespace,
                version_label=version.version_label,
                content_sha256=version.content_sha256,
            )
            for library, version in rows
        }
        for pin in pins:
            key = (pin.namespace, pin.version)
            match = resolved.get(key)
            if match is None:
                raise LibraryVersionNotFoundError(
                    f"library version {pin.namespace}@{pin.version} was not found"
                )
            if match.content_sha256 != pin.content_sha256:
                raise PersistenceIntegrityError(
                    f"content hash mismatch for {pin.namespace}@{pin.version}"
                )
        return resolved

    def _resolve_entries(
        self,
        annotations: tuple[SignalAnnotation, ...],
        versions: dict[tuple[str, str], _ResolvedVersion],
    ) -> dict[str, LibraryEntryRow]:
        if not annotations:
            return {}
        wanted: set[tuple[UUID, str]] = set()
        for annotation in annotations:
            version = versions.get(
                (annotation.concept_ref.namespace, annotation.concept_ref.version)
            )
            if version is None:
                raise LibraryVersionNotFoundError(
                    f"library version for {annotation.concept_ref} was not pinned"
                )
            wanted.add((version.version_id, annotation.concept_ref.entry_key))
        version_ids = {version_id for version_id, _ in wanted}
        entry_keys = {entry_key for _, entry_key in wanted}
        rows = tuple(
            self._session.scalars(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.library_version_id.in_(version_ids),
                    LibraryEntryRow.entry_key.in_(entry_keys),
                )
            )
        )
        by_key = {(row.library_version_id, row.entry_key): row for row in rows}
        resolved: dict[str, LibraryEntryRow] = {}
        for annotation in annotations:
            version = versions[(annotation.concept_ref.namespace, annotation.concept_ref.version)]
            entry = by_key.get((version.version_id, annotation.concept_ref.entry_key))
            if entry is None:
                raise PersistenceIntegrityError(
                    f"concept reference {annotation.concept_ref} does not resolve"
                )
            if annotation.geometry.type not in entry.allowed_geometry_types:
                raise PersistenceIntegrityError(
                    f"geometry {annotation.geometry.type} is not allowed for "
                    f"{annotation.concept_ref}"
                )
            resolved[str(annotation.concept_ref)] = entry
        return resolved

    def _insert_revision(
        self,
        snapshot: AnnotatedRecordingSnapshot,
        versions: dict[tuple[str, str], _ResolvedVersion],
        entries: dict[str, LibraryEntryRow],
    ) -> None:
        revision = snapshot.revision
        self._session.add(
            RecordingRevisionRow(
                id=revision.id,
                recording_id=snapshot.recording_id,
                revision_number=revision.number,
                parent_revision_id=revision.parent_id,
                schema_version=snapshot.schema_version,
                name=snapshot.name,
                default_speaker_ref=snapshot.default_speaker_ref,
                language=snapshot.language,
                created_at=revision.created_at,
                author=revision.author,
                message=revision.message,
            )
        )
        self._session.flush()
        self._session.add_all(
            RecordingRevisionLibraryRow(
                recording_revision_id=revision.id,
                library_version_id=versions[(pin.namespace, pin.version)].version_id,
                position=position,
            )
            for position, pin in enumerate(snapshot.libraries)
        )
        self._session.flush()
        self._session.add_all(
            AnnotationRow(
                **annotation_row_values(
                    annotation,
                    recording_revision_id=revision.id,
                    position=position,
                    library_version_id=versions[
                        (annotation.concept_ref.namespace, annotation.concept_ref.version)
                    ].version_id,
                    library_entry_id=entries[str(annotation.concept_ref)].id,
                )
            )
            for position, annotation in enumerate(snapshot.annotations)
        )
        self._session.flush()


def _is_revision_race(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    return constraint in {
        "uq_recording_revisions_parent_revision_id_not_null",
        "uq_recording_revisions_recording_id_revision_number",
    }
