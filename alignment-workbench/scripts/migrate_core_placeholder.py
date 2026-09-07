"""One-time, owner-only core@0.1 test-to-word transition, never run by startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import Connection, create_engine, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from application.core_library import core_publication  # noqa: E402
from application.runtime import WorkbenchSettings  # noqa: E402
from application.ssh_tunnel import SshTunnel  # noqa: E402
from application.validation import library_content_sha256  # noqa: E402
from models import LibraryEntry, LibraryVersion  # noqa: E402
from persistence.sqlalchemy.library_repository import LibraryRepository  # noqa: E402
from persistence.sqlalchemy.mappers import library_entry_row_values  # noqa: E402
from persistence.sqlalchemy.rows import (  # noqa: E402
    AnnotationLibraryRow,
    LibraryEntryRow,
    LibraryVersionRow,
)

TABLES = (
    "annotation_libraries",
    "library_versions",
    "library_entries",
    "audio_assets",
    "speakers",
    "recordings",
    "recording_revisions",
    "recording_revision_libraries",
    "annotations",
)


def replacement_publication(current: LibraryVersion) -> LibraryVersion:
    """Accept only the exact original placeholder or the already-migrated bundle."""
    target = core_publication(current.library_id)
    if current.version_label != "0.1":
        raise ValueError("Expected core version 0.1")
    if library_content_sha256(current) != current.content_sha256:
        raise ValueError("Stored publication hash does not match its definitions")
    if current.content_sha256 == target.content_sha256:
        return current
    if len(current.entries) != 1:
        raise ValueError("Existing core@0.1 is not the exact test-only placeholder")
    old = current.entries[0]
    placeholder = LibraryEntry(
        id=old.id,
        library_version_id=current.id,
        entry_key="test",
        display_name="Test annotation",
        description="A general-purpose time interval for testing manual annotation.",
        allowed_geometry_types=("time_interval",),
        attribute_schema={"type": "object"},
    )
    if old != placeholder:
        raise ValueError("Existing core@0.1 is not the exact test-only placeholder")
    entries = tuple(
        entry.model_copy(
            update={
                "library_version_id": current.id,
                **({"id": old.id} if entry.entry_key == "word" else {}),
            }
        )
        for entry in target.entries
    )
    return current.model_copy(update={"entries": entries, "content_sha256": target.content_sha256})


def dump_tables(connection: Connection, schema: str) -> dict[str, list[dict[str, Any]]]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier")
    return {
        table: sorted(
            connection.execute(
                text(f'SELECT row_to_json(t) FROM "{schema}"."{table}" t')
            ).scalars(),
            key=lambda row: json.dumps(row, sort_keys=True),
        )
        for table in TABLES
    }


def migrate(
    connection: Connection,
    *,
    backup_path: Path | None = None,
    schema: str = "registry_align",
) -> dict[str, Any]:
    """Caller owns the transaction. No backup path means inspect without writes."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Invalid schema identifier")
    if backup_path is not None:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        tables = ", ".join(f'"{schema}"."{table}"' for table in TABLES)
        connection.execute(text(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE"))
    with Session(connection) as session:
        library = session.scalar(
            select(AnnotationLibraryRow).where(AnnotationLibraryRow.namespace == "core")
        )
        if library is None:
            raise ValueError("core library is missing; nothing to migrate")
        repository = LibraryRepository(session)
        current = next(
            (v for v in repository.list_versions(library.id) if v.version_label == "0.1"), None
        )
        if current is None:
            raise ValueError("core@0.1 is missing; nothing to migrate")
        target = replacement_publication(current)
        if target is current:
            return {"status": "already_migrated", "content_sha256": current.content_sha256}
        before = dump_tables(connection, schema)
        old_entry_id = str(current.entries[0].id)
        affected = [row for row in before["annotations"] if row["library_entry_id"] == old_entry_id]
        if any(
            row["attributes"] != {} or row["geometry_type"] != "time_interval" for row in affected
        ):
            raise ValueError("Existing test annotations do not satisfy the new word definition")
        report = {
            "status": "ready",
            "database": connection.scalar(text("SELECT current_database()")),
            "schema": schema,
            "version_id": str(current.id),
            "word_entry_id": old_entry_id,
            "old_content_sha256": current.content_sha256,
            "new_content_sha256": target.content_sha256,
            "annotation_revision_rows": len(affected),
            "distinct_annotations": len({row["annotation_id"] for row in affected}),
            "affected_revisions": len({row["recording_revision_id"] for row in affected}),
            "table_counts": {table: len(rows) for table, rows in before.items()},
        }
        if backup_path is None:
            return report
        payload = json.dumps(
            {
                "format": "alignment-workbench-schema-row-backup-v1",
                "created_at": datetime.now(UTC).isoformat(),
                "transition": report,
                "tables": before,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if backup_path.read_bytes() != payload:
            raise ValueError("Backup verification failed")
        word = next(entry for entry in target.entries if entry.entry_key == "word")
        existing_row = session.get(LibraryEntryRow, word.id)
        assert existing_row is not None
        for key, value in library_entry_row_values(word, position=2).items():
            setattr(existing_row, key, value)
        # Free old position 0 before inserting silence. Keep the referenced entry UUID.
        session.flush()
        for position, entry in enumerate(target.entries):
            if entry.entry_key != "word":
                session.add(LibraryEntryRow(**library_entry_row_values(entry, position=position)))
        version_row = session.get(LibraryVersionRow, current.id)
        assert version_row is not None
        version_row.content_sha256 = target.content_sha256
        session.flush()
        session.expire_all()
        restored = repository.get_version(current.id)
        if restored != target or library_content_sha256(restored) != target.content_sha256:
            raise ValueError("Migrated publication verification failed")
        after = dump_tables(connection, schema)
        for table in TABLES:
            if (
                table not in {"library_entries", "library_versions"}
                and before[table] != after[table]
            ):
                raise ValueError(f"Unexpected changes to {table}; rolling back")
        for table, foreign_key in (
            ("library_entries", "library_version_id"),
            ("library_versions", "id"),
        ):
            old_other = [row for row in before[table] if row[foreign_key] != str(current.id)]
            new_other = [row for row in after[table] if row[foreign_key] != str(current.id)]
            if old_other != new_other:
                raise ValueError(f"Unexpected changes to other publications in {table}")
        return {
            **report,
            "status": "migrated",
            "backup": str(backup_path.resolve()),
            "backup_sha256": hashlib.sha256(payload).hexdigest(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the test-to-word transition")
    parser.add_argument(
        "--backup", type=Path, help="new schema-row JSON backup file, required to apply"
    )
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup pointing to a new file")
    try:
        root = Path(__file__).resolve().parents[1]
        values = dict(dotenv_values(root / ".env"))
        values.update(os.environ)
        settings = WorkbenchSettings.from_mapping(values)
        for directory in ("pending", "conflicts", "quarantine"):
            if any((settings.recovery_root / directory).glob("*.json")):
                raise ValueError(f"Resolve recovery files in {directory} before migrating")
        admin_url = make_url(values.get("REGISTRY_ALIGN_DATABASE_ADMIN_URL") or "")
        runtime_url = make_url(settings.database_url)
        if admin_url.drivername != "postgresql+psycopg" or (
            admin_url.host,
            admin_url.port,
            admin_url.database,
        ) != (runtime_url.host, runtime_url.port, runtime_url.database):
            raise ValueError("Admin and runtime URLs must name the same PostgreSQL target")
        with SshTunnel(settings.ssh_tunnel_config):
            engine = create_engine(admin_url)
            try:
                with engine.begin() as connection:
                    if args.apply and connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND usename = :runtime_role AND pid <> pg_backend_pid() "
                            "AND application_name NOT LIKE 'pgAdmin%'"
                        ),
                        {"runtime_role": runtime_url.username},
                    ):
                        raise ValueError(
                            "Close the running workbench before applying the migration"
                        )
                    report = migrate(connection, backup_path=args.backup if args.apply else None)
                print(json.dumps(report, indent=2))
            finally:
                engine.dispose()
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # Driver errors can contain credentials or annotation content.
        print(
            f"Migration failed ({type(exc).__name__}); transaction was rolled back.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
