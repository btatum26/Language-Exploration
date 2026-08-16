from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from src.analysis.cache import AnalysisCache
from src.analysis.formants import estimate_formants
from src.analysis.pitch import estimate_acoustic_tracks
from src.model.project import Project


def export_project_json(project: Project, path: str | Path) -> None:
    payload = {
        "schema_version": 2,
        "project_id": str(project.id),
        "playback_rate": project.playback_rate,
        "analysis_settings": asdict(project.settings),
        "timeline": {
            "frame_position": project.transport.frame_position,
            "selection_start": project.selection.start,
            "selection_end": project.selection.end,
            "viewport_start": project.viewport.start,
            "viewport_end": project.viewport.end,
            "loop_selection": project.transport.loop_selection,
            "follow_playhead": project.transport.follow_playhead,
            "active_tab": project.active_tab.value,
        },
        "tracks": [
            {
                "id": str(track.id),
                "name": track.name,
                "source_path": str(track.source_path) if track.source_path else None,
                "origin": track.origin,
                "color": track.color,
                "duration": track.duration,
                "full_duration": track.full_duration,
                "trim_start_seconds": track.trim_bounds_seconds[0],
                "trim_end_seconds": track.trim_bounds_seconds[1],
                "original_rate": track.original_rate,
                "playback_rate": track.playback_rate,
                "analysis_rate": track.analysis_rate,
                "channels": track.channels,
                "gain": track.gain,
                "muted": track.muted,
                "solo": track.solo,
                "visible": track.visible,
                "selection_start": project.selection_for(track.id).start,
                "selection_end": project.selection_for(track.id).end,
            }
            for track in project.tracks
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_acoustic_csv(project: Project, cache: AnalysisCache, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "track_id",
                "track_name",
                "time_seconds",
                "f0_hz",
                "f1_hz",
                "f2_hz",
                "f3_hz",
                "rms",
            ]
        )
        for track in project.tracks:
            key = cache.key(
                track.id,
                "acoustic",
                project.settings.window_ms,
                project.settings.hop_ms,
                track.trim_start_frame,
                track.trim_end_frame,
            )
            result = cache.get_or_compute(
                key,
                lambda track=track: estimate_acoustic_tracks(
                    track.analysis_view,
                    track.analysis_rate,
                    window_ms=project.settings.window_ms,
                    hop_ms=project.settings.hop_ms,
                ),
            )
            formant_key = cache.key(
                track.id,
                "formants",
                project.settings.window_ms,
                project.settings.hop_ms,
                track.trim_start_frame,
                track.trim_end_frame,
            )
            formants = cache.get_or_compute(
                formant_key,
                lambda track=track: estimate_formants(
                    track.analysis_view,
                    track.analysis_rate,
                    window_ms=project.settings.window_ms,
                    hop_ms=project.settings.hop_ms,
                ),
            )
            count = min(len(result.times), len(formants.times))
            for index in range(count):
                f0 = result.f0_hz[index]
                f1, f2, f3 = formants.frequencies_hz[:, index]
                writer.writerow(
                    [
                        str(track.id),
                        track.name,
                        f"{result.times[index]:.6f}",
                        "" if f0 != f0 else f"{f0:.6f}",
                        "" if f1 != f1 else f"{f1:.6f}",
                        "" if f2 != f2 else f"{f2:.6f}",
                        "" if f3 != f3 else f"{f3:.6f}",
                        f"{result.rms[index]:.8f}",
                    ]
                )
