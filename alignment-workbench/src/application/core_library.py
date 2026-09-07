"""Idempotent initialization of the exact built-in annotation publication."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.errors import PersistenceIntegrityError, WorkbenchStartupError
from application.handlers import LibraryHandler
from application.validation import library_content_sha256
from models import Library, LibraryEntry, LibraryVersion


def core_publication(library_id: UUID) -> LibraryVersion:
    version_id = uuid4()
    version = LibraryVersion(
        id=version_id,
        library_id=library_id,
        version_label="0.1",
        content_sha256="0" * 64,
        created_at=datetime.now(UTC),
        entries=(
            LibraryEntry(
                id=uuid4(),
                library_version_id=version_id,
                entry_key="test",
                display_name="Test annotation",
                description="A general-purpose time interval for testing manual annotation.",
                allowed_geometry_types=("time_interval",),
                attribute_schema={"type": "object"},
            ),
        ),
    )
    return version.model_copy(update={"content_sha256": library_content_sha256(version)})


def ensure_core_library(libraries: LibraryHandler) -> LibraryVersion:
    """Reuse immutable content; unique constraints arbitrate competing initializers."""
    library = next((item for item in libraries.list() if item.namespace == "core"), None)
    if library is None:
        try:
            library = libraries.create(
                Library(
                    id=uuid4(),
                    namespace="core",
                    name="Core annotations",
                )
            )
        except PersistenceIntegrityError:
            library = next((item for item in libraries.list() if item.namespace == "core"), None)
            if library is None:
                raise

    expected = core_publication(library.id)
    versions = libraries.list_versions(library.id)
    version = next((v for v in versions if v.version_label == "0.1"), None)
    if version is None:
        duplicate = next((v for v in versions if v.content_sha256 == expected.content_sha256), None)
        if duplicate is not None:
            raise WorkbenchStartupError(
                "Core library initialization failed: required content already exists as "
                f"core@{duplicate.version_label}. Storage requires unique content hashes per "
                "library, so core@0.1 cannot be published. Existing publications were preserved."
            )
        try:
            version = libraries.publish_version(expected)
        except PersistenceIntegrityError:
            version = next(
                (v for v in libraries.list_versions(library.id) if v.version_label == "0.1"), None
            )
            if version is None:
                raise
    if (
        version.content_sha256 != expected.content_sha256
        or library_content_sha256(version) != expected.content_sha256
    ):
        raise WorkbenchStartupError(
            "Core library initialization failed: core@0.1 conflicts with the required "
            "Core annotations content. Existing content was preserved."
        )
    return version
