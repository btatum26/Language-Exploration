"""Immutable, persistence-independent models for annotated audio snapshots."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)

_NAMESPACE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_VERSION_LABEL_PATTERN = r"[^:\s]+"
_ENTRY_KEY_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"

Sha256 = Annotated[
    str,
    StringConstraints(to_lower=True, pattern=r"^[0-9a-fA-F]{64}$"),
]
Namespace = Annotated[str, StringConstraints(pattern=rf"^{_NAMESPACE_PATTERN}$")]
VersionLabel = Annotated[str, StringConstraints(pattern=rf"^{_VERSION_LABEL_PATTERN}$")]
EntryKey = Annotated[str, StringConstraints(pattern=rf"^{_ENTRY_KEY_PATTERN}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]
GeometryType = Literal[
    "point",
    "time_interval",
    "time_frequency_box",
    "time_frequency_polygon",
]

_CONCEPT_REF_PATTERN = re.compile(
    rf"^(?P<namespace>{_NAMESPACE_PATTERN})"
    rf"@(?P<version>{_VERSION_LABEL_PATTERN})"
    rf":(?P<entry_key>{_ENTRY_KEY_PATTERN})$"
)


class _FrozenJsonObject(dict[str, Any]):
    """A JSON object that cannot be changed after domain validation."""

    @staticmethod
    def _reject_mutation(*args: Any, **kwargs: Any) -> None:
        raise TypeError("JSON objects in domain models are immutable")

    __delitem__ = _reject_mutation
    __ior__ = _reject_mutation  # type: ignore[assignment]
    __setitem__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation  # type: ignore[assignment]
    setdefault = _reject_mutation
    update = _reject_mutation


def _ensure_json_value(value: Any, path: str = "$") -> None:
    """Reject values that cannot be represented by standards-compliant JSON."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string object key")
            _ensure_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains non-JSON value {type(value).__name__}")


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, dict):
        return _FrozenJsonObject({key: _freeze_json_value(item) for key, item in value.items()})
    return value


def _validate_json_object(value: Any) -> Any:
    if not isinstance(value, dict):
        raise ValueError("value must be a JSON object")
    _ensure_json_value(value)
    return value


def _freeze_json_object(value: dict[str, Any]) -> _FrozenJsonObject:
    frozen = _freeze_json_value(value)
    assert isinstance(frozen, _FrozenJsonObject)
    return frozen


def _validate_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a UTC offset")
    return value


class DomainModel(BaseModel):
    """Common strict and immutable behavior for domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    def to_deterministic_json(self) -> str:
        """Serialize with stable key ordering and no insignificant whitespace."""

        return json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class AudioAsset(DomainModel):
    """Identity and native timebase for immutable audio bytes."""

    id: UUID
    sha256: Sha256
    storage_uri: NonEmptyStr
    logical_path: str | None = None
    media_type: str | None = None
    original_extension: str | None = None
    codec: str | None = None
    sample_rate_hz: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    channels: int = Field(gt=0)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    _source_metadata_is_json = field_validator("source_metadata", mode="before")(
        _validate_json_object
    )
    _source_metadata_is_frozen = field_validator("source_metadata")(_freeze_json_object)

    @property
    def duration_seconds(self) -> float:
        """Duration derived from the authoritative native timebase."""

        return self.frame_count / self.sample_rate_hz


class Speaker(DomainModel):
    """Reusable speaker identity without annotation semantics."""

    id: UUID
    external_key: str | None = None
    display_name: NonEmptyStr
    metadata: dict[str, Any] = Field(default_factory=dict)

    _metadata_is_json = field_validator("metadata", mode="before")(_validate_json_object)
    _metadata_is_frozen = field_validator("metadata")(_freeze_json_object)


class ConceptRef(RootModel[str]):
    """Portable pointer in ``namespace@version:entry-key`` form."""

    model_config = ConfigDict(frozen=True)

    @field_validator("root")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        if _CONCEPT_REF_PATTERN.fullmatch(value) is None:
            raise ValueError("concept reference must use namespace@version:entry-key")
        return value

    @property
    def namespace(self) -> str:
        return self._parts[0]

    @property
    def version(self) -> str:
        return self._parts[1]

    @property
    def entry_key(self) -> str:
        return self._parts[2]

    @property
    def _parts(self) -> tuple[str, str, str]:
        namespace, remainder = self.root.split("@", maxsplit=1)
        version, entry_key = remainder.split(":", maxsplit=1)
        return namespace, version, entry_key

    def __str__(self) -> str:
        return self.root


class PointGeometry(DomainModel):
    type: Literal["point"] = "point"
    start_sample: int = Field(ge=0)
    end_sample: None = None


class TimeIntervalGeometry(DomainModel):
    type: Literal["time_interval"] = "time_interval"
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> TimeIntervalGeometry:
        if self.end_sample <= self.start_sample:
            raise ValueError("end_sample must be greater than start_sample")
        return self


class TimeFrequencyBoxGeometry(DomainModel):
    type: Literal["time_frequency_box"] = "time_frequency_box"
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    min_frequency_hz: float = Field(ge=0, allow_inf_nan=False)
    max_frequency_hz: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_bounds(self) -> TimeFrequencyBoxGeometry:
        if self.end_sample <= self.start_sample:
            raise ValueError("end_sample must be greater than start_sample")
        if self.max_frequency_hz <= self.min_frequency_hz:
            raise ValueError("max_frequency_hz must be greater than min_frequency_hz")
        return self


class TimeFrequencyVertex(DomainModel):
    sample: int = Field(ge=0)
    frequency_hz: float = Field(ge=0, allow_inf_nan=False)


class TimeFrequencyPolygonGeometry(DomainModel):
    type: Literal["time_frequency_polygon"] = "time_frequency_polygon"
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    min_frequency_hz: float = Field(ge=0, allow_inf_nan=False)
    max_frequency_hz: float = Field(gt=0, allow_inf_nan=False)
    vertices: tuple[TimeFrequencyVertex, ...] = Field(min_length=3)

    @model_validator(mode="after")
    def validate_bounds(self) -> TimeFrequencyPolygonGeometry:
        if self.end_sample <= self.start_sample:
            raise ValueError("end_sample must be greater than start_sample")
        if self.max_frequency_hz <= self.min_frequency_hz:
            raise ValueError("max_frequency_hz must be greater than min_frequency_hz")
        for vertex in self.vertices:
            if not self.start_sample <= vertex.sample <= self.end_sample:
                raise ValueError("polygon vertex sample lies outside declared bounds")
            if not self.min_frequency_hz <= vertex.frequency_hz <= self.max_frequency_hz:
                raise ValueError("polygon vertex frequency lies outside declared bounds")
        return self


Geometry = Annotated[
    PointGeometry | TimeIntervalGeometry | TimeFrequencyBoxGeometry | TimeFrequencyPolygonGeometry,
    Field(discriminator="type"),
]


class SignalAnnotation(DomainModel):
    id: UUID
    concept_ref: ConceptRef
    geometry: Geometry
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None
    provenance_ref: str | None = None

    _attributes_are_json = field_validator("attributes", mode="before")(_validate_json_object)
    _attributes_are_frozen = field_validator("attributes")(_freeze_json_object)


class Library(DomainModel):
    id: UUID
    namespace: Namespace
    name: NonEmptyStr
    description: str | None = None
    owner_label: str | None = None


class LibraryEntry(DomainModel):
    id: UUID
    library_version_id: UUID
    entry_key: EntryKey
    display_name: NonEmptyStr
    description: str
    allowed_geometry_types: tuple[GeometryType, ...] = Field(min_length=1)
    attribute_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    validation_hints: dict[str, Any] = Field(default_factory=dict)
    display_hints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _json_objects_are_json = field_validator(
        "attribute_schema",
        "validation_hints",
        "display_hints",
        "metadata",
        mode="before",
    )(_validate_json_object)
    _json_objects_are_frozen = field_validator(
        "attribute_schema",
        "validation_hints",
        "display_hints",
        "metadata",
    )(_freeze_json_object)


class LibraryVersion(DomainModel):
    id: UUID
    library_id: UUID
    version_label: VersionLabel
    content_sha256: Sha256
    created_at: datetime
    author: str | None = None
    description: str | None = None
    entries: tuple[LibraryEntry, ...] = ()

    _created_at_has_offset = field_validator("created_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def entries_belong_to_version(self) -> LibraryVersion:
        if any(entry.library_version_id != self.id for entry in self.entries):
            raise ValueError("every entry must belong to this library version")
        return self


class PinnedLibraryVersion(DomainModel):
    """Portable snapshot manifest reference to one immutable publication."""

    namespace: Namespace
    version: VersionLabel
    content_sha256: Sha256


class RevisionMetadata(DomainModel):
    id: UUID
    number: int = Field(gt=0)
    parent_id: UUID | None = None
    created_at: datetime
    author: str | None = None
    message: str | None = None

    _created_at_has_offset = field_validator("created_at")(_validate_aware_datetime)

    @model_validator(mode="after")
    def validate_parent(self) -> RevisionMetadata:
        if self.number == 1 and self.parent_id is not None:
            raise ValueError("revision 1 cannot have a parent")
        if self.number > 1 and self.parent_id is None:
            raise ValueError("revisions after revision 1 require a parent")
        return self


class AnnotatedRecordingSnapshot(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    recording_id: UUID
    revision: RevisionMetadata
    name: NonEmptyStr
    default_speaker_ref: UUID | None = None
    language: NonEmptyStr
    audio_asset: AudioAsset
    libraries: tuple[PinnedLibraryVersion, ...]
    annotations: tuple[SignalAnnotation, ...]

    @model_validator(mode="after")
    def validate_aggregate(self) -> AnnotatedRecordingSnapshot:
        _validate_snapshot_contents(self.libraries, self.annotations, self.audio_asset)
        return self


def _validate_snapshot_contents(
    libraries: tuple[PinnedLibraryVersion, ...],
    annotations: tuple[SignalAnnotation, ...],
    audio_asset: AudioAsset | None = None,
) -> None:
    annotation_ids = [annotation.id for annotation in annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ValueError("annotation IDs must be unique within a snapshot")

    pinned_versions = [(library.namespace, library.version) for library in libraries]
    if len(pinned_versions) != len(set(pinned_versions)):
        raise ValueError("pinned namespace@version pairs must be unique")
    pinned_version_set = set(pinned_versions)

    for annotation in annotations:
        concept_version = (
            annotation.concept_ref.namespace,
            annotation.concept_ref.version,
        )
        if concept_version not in pinned_version_set:
            raise ValueError("annotation concept references an unpinned library version")
        if audio_asset is None:
            continue
        geometry = annotation.geometry
        if isinstance(geometry, PointGeometry):
            if geometry.start_sample >= audio_asset.frame_count:
                raise ValueError("point geometry lies beyond the audio frame count")
        elif geometry.end_sample > audio_asset.frame_count:
            raise ValueError("annotation geometry lies beyond the audio frame count")


class CreateRecordingRequest(DomainModel):
    """Complete initial state submitted to create a recording and revision one."""

    recording_id: UUID
    audio_asset: AudioAsset
    name: NonEmptyStr
    default_speaker_ref: UUID | None = None
    language: NonEmptyStr
    author: str | None = None
    message: str | None = None
    libraries: tuple[PinnedLibraryVersion, ...]
    annotations: tuple[SignalAnnotation, ...]

    @model_validator(mode="after")
    def validate_aggregate(self) -> CreateRecordingRequest:
        _validate_snapshot_contents(self.libraries, self.annotations, self.audio_asset)
        return self


class SaveRecordingSnapshotRequest(DomainModel):
    """Complete editable state submitted to create the next revision."""

    recording_id: UUID
    expected_parent_revision_id: UUID | None
    name: NonEmptyStr
    default_speaker_ref: UUID | None = None
    language: NonEmptyStr
    author: str | None = None
    message: str | None = None
    libraries: tuple[PinnedLibraryVersion, ...]
    annotations: tuple[SignalAnnotation, ...]

    @model_validator(mode="after")
    def validate_snapshot_local_invariants(self) -> SaveRecordingSnapshotRequest:
        _validate_snapshot_contents(self.libraries, self.annotations)
        return self
