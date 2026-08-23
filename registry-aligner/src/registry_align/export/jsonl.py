"""Canonical JSONL exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from registry_align.storage.atomic import atomic_write_jsonl


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json") and isinstance(result[key], str):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
    return result


def export_jsonl(
    output_directory: Path,
    recordings: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> tuple[Path, Path]:
    recordings_path = output_directory / "recordings.jsonl"
    segments_path = output_directory / "segments.jsonl"
    atomic_write_jsonl(recordings_path, (_decode_json_columns(row) for row in recordings))
    atomic_write_jsonl(segments_path, (_decode_json_columns(row) for row in segments))
    return recordings_path, segments_path
