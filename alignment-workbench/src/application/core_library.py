"""Idempotent initialization of the exact built-in annotation publication."""

from uuid import UUID

from application.bundled_libraries import BUNDLE_ROOT, load_bundle
from application.errors import PersistenceIntegrityError, WorkbenchStartupError
from application.handlers import LibraryHandler
from application.validation import library_content_sha256
from models import Library, LibraryVersion


def core_publication(library_id: UUID) -> LibraryVersion:
    return load_bundle(BUNDLE_ROOT / "core" / "0.1", library_id)[1]


def ensure_core_library(libraries: LibraryHandler) -> LibraryVersion:
    return ensure_publication(libraries, load_bundle(BUNDLE_ROOT / "core" / "0.1"))


def ensure_bundled_libraries(libraries: LibraryHandler) -> tuple[LibraryVersion, ...]:
    # Validate every bundle before the first persistence write.
    bundles = tuple(
        load_bundle(BUNDLE_ROOT / name / "0.1") for name in ("core", "phonetics", "prosody")
    )
    return tuple(ensure_publication(libraries, bundle) for bundle in bundles)


def ensure_publication(
    libraries: LibraryHandler,
    bundle: tuple[Library, LibraryVersion],
) -> LibraryVersion:
    """Reuse immutable content; unique constraints arbitrate competing initializers."""
    authored, expected = bundle
    namespace, label = authored.namespace, expected.version_label
    library = next((item for item in libraries.list() if item.namespace == namespace), None)
    if library is None:
        try:
            library = libraries.create(authored)
        except PersistenceIntegrityError:
            library = next((item for item in libraries.list() if item.namespace == namespace), None)
            if library is None:
                raise

    expected = expected.model_copy(update={"library_id": library.id})
    versions = libraries.list_versions(library.id)
    version = next((v for v in versions if v.version_label == label), None)
    if version is None:
        duplicate = next((v for v in versions if v.content_sha256 == expected.content_sha256), None)
        if duplicate is not None:
            raise WorkbenchStartupError(
                "Bundled library initialization failed: required content already exists as "
                f"{namespace}@{duplicate.version_label}. Storage requires unique content hashes "
                f"per library, so {namespace}@{label} cannot be published. "
                "Existing publications were preserved."
            )
        try:
            version = libraries.publish_version(expected)
        except PersistenceIntegrityError:
            version = next(
                (v for v in libraries.list_versions(library.id) if v.version_label == label), None
            )
            if version is None:
                raise
    if (
        version.content_sha256 != expected.content_sha256
        or library_content_sha256(version) != expected.content_sha256
    ):
        raise WorkbenchStartupError(
            f"{namespace}@{label} conflicts with the required bundled content. "
            + (
                "The existing publication is the old test-only placeholder. "
                if namespace == "core" and [e.entry_key for e in version.entries] == ["test"]
                else ""
            )
            + "Existing content and recordings were preserved. Select a fresh development "
            "database with REGISTRY_ALIGN_DATABASE_URL, apply existing migrations, and restart. "
            "See docs/application-api/Bundled_Libraries.md for transition commands."
        )
    return version
