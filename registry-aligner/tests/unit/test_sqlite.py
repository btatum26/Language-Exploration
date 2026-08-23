from pathlib import Path

from registry_align.storage.sqlite import SQLiteRepository


def test_migrations_are_idempotent_and_enable_foreign_keys(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "corpus.sqlite3")

    repository.migrate()
    repository.migrate()

    with repository.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert [row[0] for row in versions] == [1]
    assert {"runs", "recordings", "transcripts", "segments", "cache_entries"} <= tables
