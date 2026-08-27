from __future__ import annotations

import csv
import json

import numpy as np

from spectrogram_playground.analysis.cache import AnalysisCache
from spectrogram_playground.audio.decoding import track_from_samples
from spectrogram_playground.exporting import export_acoustic_csv, export_project_json
from spectrogram_playground.model import Project


def test_json_export_contains_metadata_but_not_audio(tmp_path) -> None:
    project = Project(playback_rate=16_000)
    track = track_from_samples(
        np.zeros(1_600, dtype=np.float32),
        16_000,
        name="silence",
        project_rate=16_000,
        analysis_rate=16_000,
    )
    project.add_track(track)
    target = tmp_path / "project.json"
    export_project_json(project, target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["tracks"][0]["id"] == str(track.id)
    assert payload["tracks"][0]["trim_start_seconds"] == 0
    assert payload["tracks"][0]["trim_end_seconds"] == track.full_duration
    assert "samples" not in target.read_text(encoding="utf-8")


def test_csv_export_has_f0_formant_and_rms_columns(tmp_path) -> None:
    project = Project(playback_rate=16_000)
    project.add_track(
        track_from_samples(
            np.zeros(1_600, dtype=np.float32),
            16_000,
            name="silence",
            project_rate=16_000,
            analysis_rate=16_000,
        )
    )
    target = tmp_path / "features.csv"
    export_acoustic_csv(project, AnalysisCache(), target)
    rows = list(csv.DictReader(target.open(encoding="utf-8")))
    assert rows
    assert set(rows[0]) == {
        "track_id",
        "track_name",
        "time_seconds",
        "f0_hz",
        "f1_hz",
        "f2_hz",
        "f3_hz",
        "rms",
    }
