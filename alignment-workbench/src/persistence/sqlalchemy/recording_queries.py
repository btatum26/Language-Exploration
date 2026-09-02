"""Bounded SQL query plans for recording snapshot and workspace hydration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, false, select
from sqlalchemy.orm import Session

from application.errors import RecordingNotFoundError, RevisionNotFoundError
from application.read_models import RecordingWorkspace
from models import AnnotatedRecordingSnapshot, LibraryVersion, Speaker
from persistence.sqlalchemy.mappers import (
    annotation_from_rows,
    audio_asset_from_row,
    library_version_from_rows,
    pinned_library_from_rows,
    revision_metadata_from_row,
    speaker_from_row,
)
from persistence.sqlalchemy.rows import (
    AnnotationLibraryRow,
    AnnotationRow,
    AudioAssetRow,
    LibraryEntryRow,
    LibraryVersionRow,
    RecordingRevisionLibraryRow,
    RecordingRevisionRow,
    RecordingRow,
    SpeakerRow,
)


@dataclass(frozen=True, slots=True)
class HydratedRecording:
    snapshot: AnnotatedRecordingSnapshot
    default_speaker: Speaker | None
    library_versions: tuple[LibraryVersion, ...]


def load_recording(
    session: Session,
    recording_id: UUID,
    revision_id: UUID | None,
    *,
    include_library_entries: bool,
) -> HydratedRecording:
    """Load one snapshot in three queries, or a workspace in four."""

    revision_join = and_(
        RecordingRevisionRow.recording_id == RecordingRow.id,
        RecordingRevisionRow.id
        == (revision_id if revision_id is not None else RecordingRow.head_revision_id),
    )
    header = session.execute(
        select(RecordingRow, RecordingRevisionRow, AudioAssetRow, SpeakerRow)
        .join(AudioAssetRow, AudioAssetRow.id == RecordingRow.audio_asset_id)
        .join(RecordingRevisionRow, revision_join)
        .outerjoin(SpeakerRow, SpeakerRow.id == RecordingRevisionRow.default_speaker_ref)
        .where(RecordingRow.id == recording_id)
    ).one_or_none()
    if header is None:
        exists = session.scalar(select(RecordingRow.id).where(RecordingRow.id == recording_id))
        if exists is None:
            raise RecordingNotFoundError(f"recording {recording_id} was not found")
        selected = revision_id if revision_id is not None else "current head"
        raise RevisionNotFoundError(
            f"revision {selected} was not found for recording {recording_id}"
        )

    recording, revision, audio_row, speaker_row = header
    pin_rows = tuple(
        session.execute(
            select(RecordingRevisionLibraryRow, LibraryVersionRow, AnnotationLibraryRow)
            .join(
                LibraryVersionRow,
                LibraryVersionRow.id == RecordingRevisionLibraryRow.library_version_id,
            )
            .join(
                AnnotationLibraryRow,
                AnnotationLibraryRow.id == LibraryVersionRow.library_id,
            )
            .where(RecordingRevisionLibraryRow.recording_revision_id == revision.id)
            .order_by(RecordingRevisionLibraryRow.position)
        )
    )
    annotation_rows = tuple(
        session.execute(
            select(AnnotationRow, AnnotationLibraryRow, LibraryVersionRow, LibraryEntryRow)
            .join(LibraryEntryRow, LibraryEntryRow.id == AnnotationRow.library_entry_id)
            .join(LibraryVersionRow, LibraryVersionRow.id == AnnotationRow.library_version_id)
            .join(
                AnnotationLibraryRow,
                AnnotationLibraryRow.id == LibraryVersionRow.library_id,
            )
            .where(AnnotationRow.recording_revision_id == revision.id)
            .order_by(AnnotationRow.position)
        )
    )

    snapshot = AnnotatedRecordingSnapshot(
        schema_version=revision.schema_version,
        recording_id=recording.id,
        revision=revision_metadata_from_row(revision),
        name=revision.name,
        default_speaker_ref=revision.default_speaker_ref,
        language=revision.language,
        audio_asset=audio_asset_from_row(audio_row),
        libraries=tuple(
            pinned_library_from_rows(library, version) for _, version, library in pin_rows
        ),
        annotations=tuple(
            annotation_from_rows(annotation, library, version, entry)
            for annotation, library, version, entry in annotation_rows
        ),
    )

    versions: tuple[LibraryVersion, ...] = ()
    if include_library_entries and pin_rows:
        version_ids = [version.id for _, version, _ in pin_rows]
        entries_by_version: dict[UUID, list[LibraryEntryRow]] = defaultdict(list)
        for entry in session.scalars(
            select(LibraryEntryRow)
            .where(LibraryEntryRow.library_version_id.in_(version_ids))
            .order_by(LibraryEntryRow.library_version_id, LibraryEntryRow.position)
        ):
            entries_by_version[entry.library_version_id].append(entry)
        versions = tuple(
            library_version_from_rows(version, entries_by_version[version.id])
            for _, version, _ in pin_rows
        )
    elif include_library_entries:
        # Preserve the four-query workspace contract even for a snapshot with no pins.
        tuple(session.scalars(select(LibraryEntryRow).where(false())))

    return HydratedRecording(
        snapshot=snapshot,
        default_speaker=(speaker_from_row(speaker_row) if speaker_row is not None else None),
        library_versions=versions,
    )


def load_snapshot(
    session: Session,
    recording_id: UUID,
    revision_id: UUID | None,
) -> AnnotatedRecordingSnapshot:
    return load_recording(
        session,
        recording_id,
        revision_id,
        include_library_entries=False,
    ).snapshot


def load_workspace(
    session: Session,
    recording_id: UUID,
    revision_id: UUID | None,
) -> RecordingWorkspace:
    loaded = load_recording(
        session,
        recording_id,
        revision_id,
        include_library_entries=True,
    )
    return RecordingWorkspace(
        snapshot=loaded.snapshot,
        default_speaker=loaded.default_speaker,
        library_versions=loaded.library_versions,
    )
