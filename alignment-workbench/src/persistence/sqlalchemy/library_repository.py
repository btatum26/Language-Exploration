"""Focused immutable annotation-library persistence operations."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from application.errors import (
    LibraryNotFoundError,
    LibraryVersionNotFoundError,
    PersistenceIntegrityError,
)
from models import Library, LibraryVersion
from persistence.sqlalchemy.mappers import (
    library_entry_row_values,
    library_from_row,
    library_version_from_rows,
)
from persistence.sqlalchemy.rows import (
    AnnotationLibraryRow,
    LibraryEntryRow,
    LibraryVersionRow,
)


class LibraryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, library: Library) -> Library:
        row = AnnotationLibraryRow(
            id=library.id,
            namespace=library.namespace,
            name=library.name,
            description=library.description,
            owner_label=library.owner_label,
        )
        self._session.add(row)
        self._session.flush()
        return library_from_row(row)

    def get(self, library_id: UUID) -> Library:
        row = self._session.get(AnnotationLibraryRow, library_id)
        if row is None:
            raise LibraryNotFoundError(f"library {library_id} was not found")
        return library_from_row(row)

    def list(self) -> tuple[Library, ...]:
        rows = self._session.scalars(
            select(AnnotationLibraryRow).order_by(
                AnnotationLibraryRow.namespace, AnnotationLibraryRow.id
            )
        )
        return tuple(library_from_row(row) for row in rows)

    def publish_version(self, version: LibraryVersion) -> LibraryVersion:
        if self._session.get(AnnotationLibraryRow, version.library_id) is None:
            raise LibraryNotFoundError(f"library {version.library_id} was not found")
        if any(entry.library_version_id != version.id for entry in version.entries):
            raise PersistenceIntegrityError("library entry belongs to a different version")
        version_row = LibraryVersionRow(
            id=version.id,
            library_id=version.library_id,
            version_label=version.version_label,
            content_sha256=version.content_sha256,
            created_at=version.created_at,
            author=version.author,
            description=version.description,
        )
        self._session.add(version_row)
        self._session.add_all(
            LibraryEntryRow(**library_entry_row_values(entry, position=position))
            for position, entry in enumerate(version.entries)
        )
        self._session.flush()
        return self.get_version(version.id)

    def get_version(self, library_version_id: UUID) -> LibraryVersion:
        version = self._session.get(LibraryVersionRow, library_version_id)
        if version is None:
            raise LibraryVersionNotFoundError(f"library version {library_version_id} was not found")
        entries = tuple(
            self._session.scalars(
                select(LibraryEntryRow)
                .where(LibraryEntryRow.library_version_id == library_version_id)
                .order_by(LibraryEntryRow.position)
            )
        )
        return library_version_from_rows(version, entries)

    def list_versions(self, library_id: UUID) -> tuple[LibraryVersion, ...]:
        if self._session.get(AnnotationLibraryRow, library_id) is None:
            raise LibraryNotFoundError(f"library {library_id} was not found")
        versions = tuple(
            self._session.scalars(
                select(LibraryVersionRow)
                .where(LibraryVersionRow.library_id == library_id)
                .order_by(LibraryVersionRow.created_at, LibraryVersionRow.id)
            )
        )
        if not versions:
            return ()
        entries_by_version: dict[UUID, list[LibraryEntryRow]] = defaultdict(list)
        for entry in self._session.scalars(
            select(LibraryEntryRow)
            .where(LibraryEntryRow.library_version_id.in_([version.id for version in versions]))
            .order_by(LibraryEntryRow.library_version_id, LibraryEntryRow.position)
        ):
            entries_by_version[entry.library_version_id].append(entry)
        return tuple(
            library_version_from_rows(version, entries_by_version[version.id])
            for version in versions
        )
