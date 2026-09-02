"""Centralized translation between ORM rows and immutable domain models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from application.errors import PersistenceIntegrityError
from models import (
    AudioAsset,
    ConceptRef,
    GeometryType,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    PointGeometry,
    RevisionMetadata,
    SignalAnnotation,
    Speaker,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeFrequencyVertex,
    TimeIntervalGeometry,
)
from persistence.sqlalchemy.rows import (
    AnnotationLibraryRow,
    AnnotationRow,
    AudioAssetRow,
    LibraryEntryRow,
    LibraryVersionRow,
    RecordingRevisionRow,
    SpeakerRow,
)

DomainGeometry = (
    PointGeometry | TimeIntervalGeometry | TimeFrequencyBoxGeometry | TimeFrequencyPolygonGeometry
)


@dataclass(frozen=True, slots=True)
class GeometryColumns:
    geometry_type: str
    start_sample: int
    end_sample: int | None
    min_frequency_hz: float | None
    max_frequency_hz: float | None
    polygon_vertices: list[dict[str, int | float]] | None


def geometry_to_columns(geometry: DomainGeometry) -> GeometryColumns:
    """Flatten one discriminated geometry into the annotation columns."""

    if isinstance(geometry, PointGeometry):
        return GeometryColumns("point", geometry.start_sample, None, None, None, None)
    if isinstance(geometry, TimeIntervalGeometry):
        return GeometryColumns(
            "time_interval", geometry.start_sample, geometry.end_sample, None, None, None
        )
    if isinstance(geometry, TimeFrequencyBoxGeometry):
        return GeometryColumns(
            "time_frequency_box",
            geometry.start_sample,
            geometry.end_sample,
            geometry.min_frequency_hz,
            geometry.max_frequency_hz,
            None,
        )
    return GeometryColumns(
        "time_frequency_polygon",
        geometry.start_sample,
        geometry.end_sample,
        geometry.min_frequency_hz,
        geometry.max_frequency_hz,
        [
            {"sample": vertex.sample, "frequency_hz": vertex.frequency_hz}
            for vertex in geometry.vertices
        ],
    )


def geometry_from_columns(
    *,
    geometry_type: str,
    start_sample: int,
    end_sample: int | None,
    min_frequency_hz: float | None,
    max_frequency_hz: float | None,
    polygon_vertices: list[dict[str, Any]] | None,
) -> DomainGeometry:
    """Rebuild exactly one geometry subtype from trusted database columns."""

    if geometry_type == "point":
        return PointGeometry(start_sample=start_sample)
    if geometry_type == "time_interval" and end_sample is not None:
        return TimeIntervalGeometry(start_sample=start_sample, end_sample=end_sample)
    if (
        geometry_type == "time_frequency_box"
        and end_sample is not None
        and min_frequency_hz is not None
        and max_frequency_hz is not None
    ):
        return TimeFrequencyBoxGeometry(
            start_sample=start_sample,
            end_sample=end_sample,
            min_frequency_hz=min_frequency_hz,
            max_frequency_hz=max_frequency_hz,
        )
    if (
        geometry_type == "time_frequency_polygon"
        and end_sample is not None
        and min_frequency_hz is not None
        and max_frequency_hz is not None
        and polygon_vertices is not None
    ):
        return TimeFrequencyPolygonGeometry(
            start_sample=start_sample,
            end_sample=end_sample,
            min_frequency_hz=min_frequency_hz,
            max_frequency_hz=max_frequency_hz,
            vertices=tuple(
                TimeFrequencyVertex.model_validate(vertex) for vertex in polygon_vertices
            ),
        )
    raise PersistenceIntegrityError(f"invalid persisted geometry columns for {geometry_type!r}")


def audio_asset_from_row(row: AudioAssetRow) -> AudioAsset:
    return AudioAsset(
        id=row.id,
        sha256=row.sha256,
        storage_uri=row.storage_uri,
        logical_path=row.logical_path,
        media_type=row.media_type,
        original_extension=row.original_extension,
        codec=row.codec,
        sample_rate_hz=row.sample_rate_hz,
        frame_count=row.frame_count,
        channels=row.channels,
        source_metadata=row.source_metadata,
    )


def speaker_from_row(row: SpeakerRow) -> Speaker:
    return Speaker(
        id=row.id,
        external_key=row.external_key,
        display_name=row.display_name,
        metadata=row.metadata_json,
    )


def library_from_row(row: AnnotationLibraryRow) -> Library:
    return Library(
        id=row.id,
        namespace=row.namespace,
        name=row.name,
        description=row.description,
        owner_label=row.owner_label,
    )


def library_entry_from_row(row: LibraryEntryRow) -> LibraryEntry:
    return LibraryEntry(
        id=row.id,
        library_version_id=row.library_version_id,
        entry_key=row.entry_key,
        display_name=row.display_name,
        description=row.description,
        allowed_geometry_types=tuple(
            cast(GeometryType, value) for value in row.allowed_geometry_types
        ),
        attribute_schema=row.attribute_schema,
        validation_hints=row.validation_hints,
        display_hints=row.display_hints,
        metadata=row.metadata_json,
    )


def library_version_from_rows(
    row: LibraryVersionRow,
    entry_rows: Sequence[LibraryEntryRow],
) -> LibraryVersion:
    return LibraryVersion(
        id=row.id,
        library_id=row.library_id,
        version_label=row.version_label,
        content_sha256=row.content_sha256,
        created_at=row.created_at,
        author=row.author,
        description=row.description,
        entries=tuple(library_entry_from_row(entry) for entry in entry_rows),
    )


def revision_metadata_from_row(row: RecordingRevisionRow) -> RevisionMetadata:
    return RevisionMetadata(
        id=row.id,
        number=row.revision_number,
        parent_id=row.parent_revision_id,
        created_at=row.created_at,
        author=row.author,
        message=row.message,
    )


def pinned_library_from_rows(
    library: AnnotationLibraryRow,
    version: LibraryVersionRow,
) -> PinnedLibraryVersion:
    return PinnedLibraryVersion(
        namespace=library.namespace,
        version=version.version_label,
        content_sha256=version.content_sha256,
    )


def annotation_from_rows(
    annotation: AnnotationRow,
    library: AnnotationLibraryRow,
    version: LibraryVersionRow,
    entry: LibraryEntryRow,
) -> SignalAnnotation:
    return SignalAnnotation(
        id=annotation.annotation_id,
        concept_ref=ConceptRef(f"{library.namespace}@{version.version_label}:{entry.entry_key}"),
        geometry=geometry_from_columns(
            geometry_type=annotation.geometry_type,
            start_sample=annotation.start_sample,
            end_sample=annotation.end_sample,
            min_frequency_hz=annotation.min_frequency_hz,
            max_frequency_hz=annotation.max_frequency_hz,
            polygon_vertices=annotation.polygon_vertices,
        ),
        attributes=annotation.attributes,
        confidence=annotation.confidence,
        note=annotation.note,
        provenance_ref=annotation.provenance_ref,
    )


def audio_asset_row_values(audio_asset: AudioAsset) -> dict[str, Any]:
    data = audio_asset.model_dump(mode="json")
    return {
        "id": audio_asset.id,
        "sha256": audio_asset.sha256,
        "storage_uri": audio_asset.storage_uri,
        "logical_path": audio_asset.logical_path,
        "media_type": audio_asset.media_type,
        "original_extension": audio_asset.original_extension,
        "codec": audio_asset.codec,
        "sample_rate_hz": audio_asset.sample_rate_hz,
        "frame_count": audio_asset.frame_count,
        "channels": audio_asset.channels,
        "source_metadata": data["source_metadata"],
    }


def library_entry_row_values(entry: LibraryEntry, *, position: int) -> dict[str, Any]:
    data = entry.model_dump(mode="json")
    return {
        "id": entry.id,
        "library_version_id": entry.library_version_id,
        "position": position,
        "entry_key": entry.entry_key,
        "display_name": entry.display_name,
        "description": entry.description,
        "allowed_geometry_types": list(entry.allowed_geometry_types),
        "attribute_schema": data["attribute_schema"],
        "validation_hints": data["validation_hints"],
        "display_hints": data["display_hints"],
        "metadata_json": data["metadata"],
    }


def annotation_row_values(
    annotation: SignalAnnotation,
    *,
    recording_revision_id: object,
    position: int,
    library_version_id: object,
    library_entry_id: object,
) -> dict[str, Any]:
    geometry = geometry_to_columns(annotation.geometry)
    attributes = annotation.model_dump(mode="json")["attributes"]
    return {
        "recording_revision_id": recording_revision_id,
        "annotation_id": annotation.id,
        "position": position,
        "library_version_id": library_version_id,
        "library_entry_id": library_entry_id,
        "geometry_type": geometry.geometry_type,
        "start_sample": geometry.start_sample,
        "end_sample": geometry.end_sample,
        "min_frequency_hz": geometry.min_frequency_hz,
        "max_frequency_hz": geometry.max_frequency_hz,
        "polygon_vertices": geometry.polygon_vertices,
        "attributes": attributes,
        "confidence": annotation.confidence,
        "note": annotation.note,
        "provenance_ref": annotation.provenance_ref,
    }
