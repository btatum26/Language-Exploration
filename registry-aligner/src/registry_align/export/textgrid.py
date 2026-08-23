"""Praat long-TextGrid export from effective canonical segments."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from registry_align.storage.atomic import atomic_write_text


def _quote(label: str) -> str:
    return label.replace('"', '""')


def _tier(name: str, entries: list[dict[str, Any]], maximum: float, index: int) -> str:
    lines = [
        f"    item [{index}]:",
        '        class = "IntervalTier"',
        f'        name = "{_quote(name)}"',
        "        xmin = 0",
        f"        xmax = {maximum:.9f}",
        f"        intervals: size = {len(entries)}",
    ]
    for entry_index, entry in enumerate(entries, 1):
        lines.extend(
            [
                f"        intervals [{entry_index}]:",
                f"            xmin = {float(entry['start_s']):.9f}",
                f"            xmax = {float(entry['end_s']):.9f}",
                f'            text = "{_quote(str(entry["label"]))}"',
            ]
        )
    return "\n".join(lines)


def export_textgrids(output_directory: Path, segments: list[dict[str, Any]]) -> list[Path]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        grouped[str(segment["recording_id"])].append(segment)
    directory = output_directory / "exports" / "textgrids"
    paths: list[Path] = []
    for recording_id, recording_segments in grouped.items():
        maximum = max(float(item["end_s"]) for item in recording_segments)
        tiers: list[str] = []
        for name in ("word", "phone", "silence"):
            entries = sorted(
                (item for item in recording_segments if item["tier"] == name),
                key=lambda item: float(item["start_s"]),
            )
            tiers.append(_tier(name, entries, maximum, len(tiers) + 1))
        content = "\n".join(
            [
                'File type = "ooTextFile"',
                'Object class = "TextGrid"',
                "",
                "xmin = 0",
                f"xmax = {maximum:.9f}",
                "tiers? <exists>",
                f"size = {len(tiers)}",
                "item []:",
                *tiers,
                "",
            ]
        )
        path = directory / f"{recording_id}.TextGrid"
        atomic_write_text(path, content)
        paths.append(path)
    return paths
