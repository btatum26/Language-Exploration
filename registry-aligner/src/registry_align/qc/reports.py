"""QC report generation from stored issues."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from registry_align.storage.atomic import atomic_write_json, atomic_write_text


def write_qc_reports(output_directory: Path, issues: list[dict[str, Any]]) -> tuple[Path, Path]:
    json_path = output_directory / "qc-report.json"
    csv_path = output_directory / "qc-report.csv"
    normalized = []
    for issue in issues:
        item = dict(issue)
        details = item.get("details_json")
        if isinstance(details, str):
            item["details"] = json.loads(details)
            item.pop("details_json", None)
        normalized.append(item)
    atomic_write_json(json_path, {"schema_version": "1.0", "issues": normalized})
    stream = io.StringIO(newline="")
    fields = ["run_id", "recording_id", "code", "severity", "stage", "message", "hint"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(normalized)
    atomic_write_text(csv_path, stream.getvalue())
    return json_path, csv_path
