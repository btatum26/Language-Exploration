from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from src.models import (
    AnnotatedRecordingSnapshot,
    ConceptRef,
    Geometry,
    Library,
    LibraryEntry,
    LibraryVersion,
    PointGeometry,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeIntervalGeometry,
)

EXAMPLE_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "data-models"
    / "examples"
    / "annotated_recording_snapshot.json"
)
ANNOTATION_ID = "ed8e49d4-fab7-43df-a856-62b9d1bb27a5"
LIBRARY_VERSION_ID = "2b148c0b-7a82-4fe3-8c55-e95aa10db2c8"


def test_complete_example_snapshot_round_trips_deterministically() -> None:
    snapshot = AnnotatedRecordingSnapshot.model_validate_json(EXAMPLE_PATH.read_text("utf-8"))

    serialized = snapshot.to_deterministic_json()
    reparsed = AnnotatedRecordingSnapshot.model_validate_json(serialized)

    assert serialized == snapshot.to_deterministic_json()
    assert reparsed == snapshot
    assert snapshot.audio_asset.duration_seconds == 4.0
    assert isinstance(snapshot.annotations[0].geometry, TimeIntervalGeometry)
    assert isinstance(snapshot.annotations[1].geometry, TimeFrequencyBoxGeometry)


def example_payload() -> dict[str, Any]:
    return json.loads(EXAMPLE_PATH.read_text("utf-8"))


def test_save_request_is_the_complete_editable_part_of_a_snapshot() -> None:
    snapshot = AnnotatedRecordingSnapshot.model_validate_json(EXAMPLE_PATH.read_text("utf-8"))

    request = SaveRecordingSnapshotRequest(
        recording_id=snapshot.recording_id,
        expected_parent_revision_id=snapshot.revision.id,
        name=snapshot.name,
        default_speaker_ref=snapshot.default_speaker_ref,
        language=snapshot.language,
        author="Ben",
        message="Save current state",
        libraries=snapshot.libraries,
        annotations=snapshot.annotations,
    )

    serialized = request.to_deterministic_json()

    assert SaveRecordingSnapshotRequest.model_validate_json(serialized) == request

    missing_parent = request.model_dump(mode="json", exclude={"expected_parent_revision_id"})
    with pytest.raises(ValidationError, match="expected_parent_revision_id"):
        SaveRecordingSnapshotRequest.model_validate(missing_parent)


def test_concept_ref_is_a_string_in_snapshot_json() -> None:
    reference = ConceptRef("le.prosody@1.0.0:pitch-fall")

    assert reference.namespace == "le.prosody"
    assert reference.version == "1.0.0"
    assert reference.entry_key == "pitch-fall"
    assert reference.model_dump_json() == '"le.prosody@1.0.0:pitch-fall"'


def test_every_library_entry_is_paintable() -> None:
    entry_fields = {
        "id": "03e8972e-05ce-40d2-8987-e873b8a61c1c",
        "library_version_id": LIBRARY_VERSION_ID,
        "entry_key": "pitch-fall",
        "display_name": "Pitch lowering",
        "description": "A sustained decline in estimated fundamental frequency.",
    }

    entry = LibraryEntry(**entry_fields, allowed_geometry_types=("time_interval",))

    assert entry.allowed_geometry_types == ("time_interval",)
    with pytest.raises(ValidationError):
        LibraryEntry(**entry_fields, allowed_geometry_types=())


def test_hashes_are_canonical_lowercase() -> None:
    payload = example_payload()
    payload["audio_asset"]["sha256"] = "A" * 64
    payload["libraries"][0]["content_sha256"] = "B" * 64

    snapshot = AnnotatedRecordingSnapshot.model_validate(payload)

    assert snapshot.audio_asset.sha256 == "a" * 64
    assert snapshot.libraries[0].content_sha256 == "b" * 64


def test_library_identifiers_match_concept_ref_components() -> None:
    library = Library(
        id="446e4329-8564-4e2f-8fe1-7e706540135d",
        namespace="le.prosody",
        name="Prosody",
    )
    version = LibraryVersion(
        id=LIBRARY_VERSION_ID,
        library_id=library.id,
        version_label="1.0.0",
        content_sha256="C" * 64,
        created_at="2026-08-30T23:00:00Z",
    )
    entry = LibraryEntry(
        id="03e8972e-05ce-40d2-8987-e873b8a61c1c",
        library_version_id=version.id,
        entry_key="pitch-fall",
        display_name="Pitch lowering",
        description="A sustained decline in estimated fundamental frequency.",
        allowed_geometry_types=("time_interval",),
    )

    assert str(ConceptRef(f"{library.namespace}@{version.version_label}:{entry.entry_key}"))
    assert version.content_sha256 == "c" * 64

    with pytest.raises(ValidationError):
        Library(id=library.id, namespace="contains spaces", name="Invalid")
    with pytest.raises(ValidationError):
        LibraryVersion.model_validate({**version.model_dump(), "version_label": "contains spaces"})
    with pytest.raises(ValidationError):
        LibraryEntry(
            **{
                **entry.model_dump(),
                "entry_key": "contains spaces",
            }
        )


def test_snapshot_rejects_duplicate_annotation_ids() -> None:
    payload = example_payload()
    payload["annotations"].append(payload["annotations"][0].copy())

    with pytest.raises(ValidationError, match="annotation IDs must be unique"):
        AnnotatedRecordingSnapshot.model_validate(payload)


def test_snapshot_rejects_duplicate_pinned_versions() -> None:
    payload = example_payload()
    payload["libraries"].append(payload["libraries"][0].copy())

    with pytest.raises(ValidationError, match="pinned namespace@version pairs must be unique"):
        AnnotatedRecordingSnapshot.model_validate(payload)


def test_snapshot_rejects_geometry_beyond_audio() -> None:
    payload = example_payload()
    frame_count = payload["audio_asset"]["frame_count"]
    payload["annotations"][0]["geometry"]["end_sample"] = frame_count + 1

    with pytest.raises(ValidationError, match="beyond the audio frame count"):
        AnnotatedRecordingSnapshot.model_validate(payload)

    point_payload = example_payload()
    point_payload["annotations"][0]["geometry"] = {
        "type": "point",
        "start_sample": frame_count,
    }
    with pytest.raises(ValidationError, match="point geometry lies beyond"):
        AnnotatedRecordingSnapshot.model_validate(point_payload)


def test_snapshot_rejects_unpinned_concept_version() -> None:
    payload = example_payload()
    payload["annotations"][0]["concept_ref"] = "unlisted.library@9.0:event"

    with pytest.raises(ValidationError, match="unpinned library version"):
        AnnotatedRecordingSnapshot.model_validate(payload)


def test_snapshot_rejects_polygon_vertices_outside_declared_bounds() -> None:
    payload = example_payload()
    payload["annotations"][0]["geometry"] = {
        "type": "time_frequency_polygon",
        "start_sample": 100,
        "end_sample": 200,
        "min_frequency_hz": 100,
        "max_frequency_hz": 500,
        "vertices": [
            {"sample": 99, "frequency_hz": 100},
            {"sample": 200, "frequency_hz": 100},
            {"sample": 150, "frequency_hz": 500},
        ],
    }

    with pytest.raises(ValidationError, match="outside declared bounds"):
        AnnotatedRecordingSnapshot.model_validate(payload)

    frequency_payload = example_payload()
    frequency_payload["annotations"][0]["geometry"] = {
        "type": "time_frequency_polygon",
        "start_sample": 100,
        "end_sample": 200,
        "min_frequency_hz": 100,
        "max_frequency_hz": 500,
        "vertices": [
            {"sample": 100, "frequency_hz": 99},
            {"sample": 200, "frequency_hz": 100},
            {"sample": 150, "frequency_hz": 500},
        ],
    }
    with pytest.raises(ValidationError, match="outside declared bounds"):
        AnnotatedRecordingSnapshot.model_validate(frequency_payload)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "time_interval", "start_sample": 100, "end_sample": 100},
        {
            "type": "time_frequency_box",
            "start_sample": 100,
            "end_sample": 200,
            "min_frequency_hz": 500,
            "max_frequency_hz": 500,
        },
        {
            "type": "time_frequency_polygon",
            "start_sample": 100,
            "end_sample": 200,
            "min_frequency_hz": 100,
            "max_frequency_hz": 500,
            "vertices": [
                {"sample": 100, "frequency_hz": 100},
                {"sample": 150, "frequency_hz": 500},
            ],
        },
    ],
)
def test_invalid_geometry_structure_fails(geometry: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Geometry).validate_python(geometry)


def test_point_end_must_be_null() -> None:
    assert PointGeometry(start_sample=100, end_sample=None).end_sample is None

    with pytest.raises(ValidationError, match="end_sample"):
        TypeAdapter(Geometry).validate_python(
            {"type": "point", "start_sample": 100, "end_sample": 101}
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_invalid_confidence_fails(confidence: float) -> None:
    with pytest.raises(ValidationError):
        SignalAnnotation(
            id=ANNOTATION_ID,
            concept_ref="le.core@1.0.0:event",
            geometry=PointGeometry(start_sample=0),
            confidence=confidence,
        )


@pytest.mark.parametrize(
    "attributes",
    [
        {"when": datetime(2026, 8, 30)},
        {"samples": (1, 2)},
        {1: "non-string key"},
        {"measurement": float("nan")},
    ],
)
def test_non_json_attributes_fail(attributes: dict[object, object]) -> None:
    with pytest.raises(ValidationError):
        SignalAnnotation(
            id=ANNOTATION_ID,
            concept_ref="le.core@1.0.0:event",
            geometry=PointGeometry(start_sample=0),
            attributes=attributes,  # type: ignore[arg-type]
        )


def test_annotation_ids_must_be_uuids() -> None:
    with pytest.raises(ValidationError):
        SignalAnnotation(
            id="not-a-uuid",
            concept_ref="le.core@1.0.0:event",
            geometry=PointGeometry(start_sample=0),
        )


def test_nyquist_validation_is_deferred_to_application_service() -> None:
    payload = example_payload()
    payload["annotations"][1]["geometry"]["max_frequency_hz"] = 48_000

    snapshot = AnnotatedRecordingSnapshot.model_validate(payload)

    geometry = snapshot.annotations[1].geometry
    assert isinstance(geometry, TimeFrequencyBoxGeometry)
    assert geometry.max_frequency_hz > snapshot.audio_asset.sample_rate_hz / 2


def test_models_are_frozen() -> None:
    annotation = SignalAnnotation(
        id=ANNOTATION_ID,
        concept_ref="le.core@1.0.0:event",
        geometry=PointGeometry(start_sample=10),
        attributes={"nested": [1, 2]},
    )

    with pytest.raises(ValidationError):
        annotation.geometry = PointGeometry(start_sample=11)
    with pytest.raises(TypeError, match="immutable"):
        annotation.attributes["new"] = "value"
    assert annotation.attributes["nested"] == (1, 2)


def test_uuid_values_preserve_identity_through_json() -> None:
    snapshot = AnnotatedRecordingSnapshot.model_validate_json(EXAMPLE_PATH.read_text("utf-8"))

    assert snapshot.annotations[0].id == UUID(ANNOTATION_ID)
    assert str(snapshot.annotations[0].id) in snapshot.to_deterministic_json()


def test_polygon_geometry_uses_physical_coordinates() -> None:
    polygon = TimeFrequencyPolygonGeometry(
        start_sample=100,
        end_sample=200,
        min_frequency_hz=100,
        max_frequency_hz=500,
        vertices=[
            {"sample": 100, "frequency_hz": 100},
            {"sample": 200, "frequency_hz": 100},
            {"sample": 150, "frequency_hz": 500},
        ],
    )

    assert polygon.type == "time_frequency_polygon"
    assert polygon.vertices[2].frequency_hz == 500
