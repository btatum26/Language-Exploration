"""Flat CSV exports."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from registry_align.storage.atomic import atomic_write_text


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        atomic_write_text(path, "")
        return
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, stream.getvalue())


def export_csv(
    output_directory: Path,
    recordings: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    directory = output_directory / "exports" / "csv"
    directory.mkdir(parents=True, exist_ok=True)
    paths = (
        directory / "recordings.csv",
        directory / "segments.csv",
        directory / "issues.csv",
    )
    _write_rows(paths[0], recordings)
    _write_rows(paths[1], segments)
    _write_rows(paths[2], issues)
    return paths
