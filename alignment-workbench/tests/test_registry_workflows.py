from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import soundfile as sf
from PySide6 import QtCore, QtWidgets

from alignment_workbench.services.models import (
    CatalogPage,
    RecordingDetail,
    RecordingSummary,
    SegmentData,
)
from alignment_workbench.ui.main_window import MainWindow
from alignment_workbench.ui.recording_panel import RecordingPanel


class FakeServices:
    def __init__(self, audio_path) -> None:
        self.audio_path = audio_path
        self.revisions = []
        self.created_speakers = []

    def check_connection(self):
        return {"usable": True}

    def catalog(self, query):
        summary = self._summary()
        return CatalogPage((summary,), query.offset, 1, False)

    def recording(self, _recording_id, *, fetch_missing=False):
        summary = self._summary()
        word = SegmentData(
            id="00000000-0000-0000-0000-000000000010",
            kind="word",
            label="ciao",
            start_sample=0,
            end_sample=16_000,
            timebase_sample_rate_hz=16_000,
            provenance={"run_id": "alignment-1"},
        )
        phones = (
            SegmentData(
                id="00000000-0000-0000-0000-000000000001",
                kind="phone",
                label="t͡ʃ",
                start_sample=0,
                end_sample=8_000,
                timebase_sample_rate_hz=16_000,
                parent_id=word.id,
                confidence=0.9,
                model_segment_id="00000000-0000-0000-0000-000000000001",
                provenance={"run_id": "alignment-1"},
            ),
            SegmentData(
                id="00000000-0000-0000-0000-000000000002",
                kind="phone",
                label="ao",
                start_sample=8_000,
                end_sample=16_000,
                timebase_sample_rate_hz=16_000,
                parent_id=word.id,
                confidence=0.8,
                model_segment_id="00000000-0000-0000-0000-000000000002",
                provenance={"run_id": "alignment-1"},
            ),
        )
        return RecordingDetail(summary, self.audio_path, (word,), phones)

    def speakers(self, _text=""):
        return ({"id": "speaker-1", "display_name": "Speaker One", "language": "it"},)

    def create_speaker(self, speaker_id, display_name, language):
        result = {"id": speaker_id, "display_name": display_name, "language": language}
        self.created_speakers.append(result)
        return result

    def revision_history(self, _segment_id):
        return ()

    def save_revision(self, request):
        self.revisions.append(request)
        return {
            "revision_id": f"revision-{len(self.revisions)}",
            "review_state": request.review_state,
        }

    def ingest(self, request, *, align=True):
        return {
            "items": [
                {
                    "recording_id": request.recording_id,
                    "outcome": "processed",
                }
            ]
        }

    @staticmethod
    def _summary():
        now = datetime.now(UTC)
        return RecordingSummary(
            recording_id="recording-1",
            recording_version_id="version-1",
            speaker_id="speaker-1",
            transcript="ciao",
            language="it",
            recording_created_at=now,
            version_created_at=now,
            alignment_state="complete",
            review_state="unreviewed",
            audio_available=True,
            audio_relative_path="fixture.wav",
            duration_seconds=1.0,
        )


def test_catalog_loads_multiple_tracks_and_alignment_tiers(qtbot, tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "fixture.wav"
    sf.write(audio_path, np.sin(np.linspace(0, 20, 16_000)), 16_000)
    services = FakeServices(audio_path)
    monkeypatch.setattr(RecordingPanel, "refresh_devices", lambda self: None)
    window = MainWindow(services)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.transport.connection.text() == "DB: connected")
    qtbot.waitUntil(lambda: window.library.total == 1)

    window.load_recording("recording-1")
    qtbot.waitUntil(lambda: len(window.session.tracks) == 1, timeout=15_000)
    window.load_recording("recording-1")
    qtbot.waitUntil(lambda: len(window.session.tracks) == 2, timeout=15_000)

    assert len(window.session.tracks[0].words) == 1
    assert len(window.session.tracks[0].phones) == 2
    assert window.session.reference_track_id == window.session.tracks[0].id
    assert window.timeline.analysis.wait(30_000)
    window.close()


def test_relabel_boundary_save_and_split_create_revision_requests(
    qtbot, tmp_path, monkeypatch
) -> None:
    audio_path = tmp_path / "fixture.wav"
    sf.write(audio_path, np.zeros(16_000, dtype=np.float32), 16_000)
    services = FakeServices(audio_path)
    monkeypatch.setattr(RecordingPanel, "refresh_devices", lambda self: None)
    window = MainWindow(services)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    window.load_recording("recording-1")
    qtbot.waitUntil(lambda: len(window.session.tracks) == 1, timeout=15_000)
    track = window.session.tracks[0]
    phone = track.phones[0]
    window.session.select_segment(track.id, phone.id)
    window.inspector.label.setText("d͡ʒ")
    window.inspector._label_changed("d͡ʒ")
    window.inspector.start.setValue(phone.start_frame + 20)
    window.inspector.save("accepted")
    qtbot.waitUntil(lambda: len(services.revisions) == 1)

    request = services.revisions[0]
    assert request.label == "d͡ʒ"
    assert request.start_sample > 0
    assert request.operation == "update"

    window.session.set_playhead((phone.start_frame + phone.end_frame) // 2)
    window.inspector.split()
    qtbot.waitUntil(lambda: len(services.revisions) == 2)
    assert services.revisions[1].operation == "split"
    assert len(services.revisions[1].replacement_segments) == 2
    assert window.timeline.analysis.wait(30_000)
    window.close()


def test_text_fields_keep_normal_editing_shortcuts(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(RecordingPanel, "refresh_devices", lambda self: None)
    window = MainWindow(None)
    qtbot.addWidget(window)
    window.show()
    edit = window.recording.transcript
    edit.setFocus()
    qtbot.waitUntil(lambda: QtWidgets.QApplication.focusWidget() is edit)
    qtbot.keyClicks(edit, "ciao")
    edit.selectAll()
    qtbot.keyClick(edit, QtCore.Qt.Key.Key_C, QtCore.Qt.KeyboardModifier.ControlModifier)
    edit.clear()
    qtbot.keyClick(edit, QtCore.Qt.Key.Key_V, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert edit.text() == "ciao"
    window.close()
