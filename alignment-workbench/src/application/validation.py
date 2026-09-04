"""Application-level library binding and annotation validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema.validators import validator_for

from application.errors import (
    ConceptNotFoundError,
    ConceptNotPinnedError,
    DuplicateAnnotationError,
    GeometryNotAllowedError,
    GeometryOutOfBoundsError,
    InvalidAnnotationAttributesError,
    LibraryVersionNotFoundError,
    PersistenceIntegrityError,
)
from models import (
    AudioAsset,
    ConceptRef,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    PointGeometry,
    SignalAnnotation,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
)


@dataclass(frozen=True, slots=True)
class LibraryBinding:
    pin: PinnedLibraryVersion
    version: LibraryVersion


class LibraryCatalog:
    """Remember namespaces for complete versions that crossed the handler boundary."""

    def __init__(self) -> None:
        self._libraries: dict[UUID, Library] = {}
        self._version_namespaces: dict[UUID, str] = {}
        self._versions: dict[tuple[str, str], LibraryVersion] = {}

    def remember_library(self, library: Library) -> None:
        self._libraries[library.id] = library

    def remember_version(self, version: LibraryVersion, namespace: str) -> None:
        existing = self._version_namespaces.get(version.id)
        if existing is not None and existing != namespace:
            raise PersistenceIntegrityError(
                f"library version {version.id} was associated with multiple namespaces"
            )
        self._version_namespaces[version.id] = namespace
        self._versions[(namespace, version.version_label)] = version

    def namespace_for(self, version: LibraryVersion) -> str:
        namespace = self._version_namespaces.get(version.id)
        if namespace is None:
            raise LibraryVersionNotFoundError(
                "library version namespace is unknown; obtain the version through LibraryHandler"
            )
        return namespace

    def cached_version(self, namespace: str, version_label: str) -> LibraryVersion | None:
        return self._versions.get((namespace, version_label))


def bind_library_versions(
    pins: tuple[PinnedLibraryVersion, ...],
    versions: tuple[LibraryVersion, ...],
    catalog: LibraryCatalog,
) -> tuple[LibraryBinding, ...]:
    if len(pins) != len(versions):
        raise PersistenceIntegrityError(
            "workspace library versions do not match the pinned manifest"
        )
    bindings: list[LibraryBinding] = []
    for pin, version in zip(pins, versions, strict=True):
        if version.version_label != pin.version or version.content_sha256 != pin.content_sha256:
            raise PersistenceIntegrityError(
                "library version does not match pinned manifest entry "
                f"{pin.namespace}@{pin.version}"
            )
        catalog.remember_version(version, pin.namespace)
        bindings.append(LibraryBinding(pin=pin, version=version))
    return tuple(bindings)


def concept_index(
    bindings: tuple[LibraryBinding, ...],
) -> dict[str, LibraryEntry]:
    entries: dict[str, LibraryEntry] = {}
    for binding in bindings:
        for entry in binding.version.entries:
            reference = f"{binding.pin.namespace}@{binding.pin.version}:{entry.entry_key}"
            if reference in entries:
                raise PersistenceIntegrityError(f"duplicate concept reference {reference}")
            entries[reference] = entry
    return entries


def validate_annotation(
    annotation: SignalAnnotation,
    *,
    audio_asset: AudioAsset,
    bindings: tuple[LibraryBinding, ...],
) -> None:
    versions = {(binding.pin.namespace, binding.pin.version) for binding in bindings}
    version_key = (
        annotation.concept_ref.namespace,
        annotation.concept_ref.version,
    )
    if version_key not in versions:
        raise ConceptNotPinnedError(
            f"library version {version_key[0]}@{version_key[1]} is not pinned"
        )

    entries = concept_index(bindings)
    try:
        entry = entries[str(annotation.concept_ref)]
    except KeyError as exc:
        raise ConceptNotFoundError(f"concept {annotation.concept_ref} was not found") from exc
    if annotation.geometry.type not in entry.allowed_geometry_types:
        raise GeometryNotAllowedError(
            f"concept {annotation.concept_ref} does not allow {annotation.geometry.type}"
        )

    geometry = annotation.geometry
    if isinstance(geometry, PointGeometry):
        if geometry.start_sample >= audio_asset.frame_count:
            raise GeometryOutOfBoundsError("point geometry lies beyond the audio frame count")
    elif geometry.end_sample > audio_asset.frame_count:
        raise GeometryOutOfBoundsError("annotation geometry lies beyond the audio frame count")
    if isinstance(geometry, (TimeFrequencyBoxGeometry, TimeFrequencyPolygonGeometry)):
        nyquist_hz = audio_asset.sample_rate_hz / 2
        if geometry.max_frequency_hz > nyquist_hz:
            raise GeometryNotAllowedError(
                f"geometry exceeds the audio Nyquist frequency of {nyquist_hz:g} Hz"
            )

    schema = entry.model_dump(mode="json")["attribute_schema"]
    attributes = annotation.model_dump(mode="json")["attributes"]
    validator_type = validator_for(schema)
    try:
        validator_type.check_schema(schema)
        validator_type(schema).validate(attributes)
    except jsonschema_exceptions.SchemaError as exc:
        raise PersistenceIntegrityError(
            f"concept {annotation.concept_ref} has an invalid attribute schema"
        ) from exc
    except jsonschema_exceptions.ValidationError as exc:
        raise InvalidAnnotationAttributesError(
            f"attributes do not satisfy concept {annotation.concept_ref}: {exc.message}"
        ) from exc


def validate_annotations(
    annotations: tuple[SignalAnnotation, ...],
    *,
    audio_asset: AudioAsset,
    bindings: tuple[LibraryBinding, ...],
) -> None:
    seen: set[UUID] = set()
    for annotation in annotations:
        if annotation.id in seen:
            raise DuplicateAnnotationError(f"annotation {annotation.id} is duplicated")
        seen.add(annotation.id)
        validate_annotation(annotation, audio_asset=audio_asset, bindings=bindings)


def library_content_sha256(version: LibraryVersion) -> str:
    """Hash ordered semantic entry definitions, excluding generated identities."""

    entries: list[dict[str, Any]] = []
    for entry in version.entries:
        payload = entry.model_dump(mode="json")
        payload.pop("id")
        payload.pop("library_version_id")
        entries.append(payload)
    serialized = json.dumps(
        {"entries": entries},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def require_library_content_hash(version: LibraryVersion) -> None:
    expected = library_content_sha256(version)
    if version.content_sha256 != expected:
        raise PersistenceIntegrityError(f"library version content hash must be {expected}")


def pin_for_version(version: LibraryVersion, namespace: str) -> PinnedLibraryVersion:
    return PinnedLibraryVersion(
        namespace=namespace,
        version=version.version_label,
        content_sha256=version.content_sha256,
    )


def resolve_concept(
    reference: ConceptRef,
    bindings: tuple[LibraryBinding, ...],
) -> LibraryEntry:
    versions = {(binding.pin.namespace, binding.pin.version) for binding in bindings}
    if (reference.namespace, reference.version) not in versions:
        raise ConceptNotPinnedError(
            f"library version {reference.namespace}@{reference.version} is not pinned"
        )
    try:
        return concept_index(bindings)[str(reference)]
    except KeyError as exc:
        raise ConceptNotFoundError(f"concept {reference} was not found") from exc
