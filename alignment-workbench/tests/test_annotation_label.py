from uuid import uuid4

from PySide6 import QtGui

from gui.waveform import WaveformEnvelope, WaveformView
from models import ConceptRef, SignalAnnotation, TimeIntervalGeometry


def test_old_annotation_json_and_explicit_label_round_trip():
    old = {
        "id": str(uuid4()),
        "concept_ref": "core@0.1:test",
        "geometry": {"type": "time_interval", "start_sample": 100, "end_sample": 500},
        "note": "Keep this note",
        "attributes": {"label": "An unrelated concept attribute"},
    }
    annotation = SignalAnnotation.model_validate(old)
    assert annotation.label is None
    labeled = annotation.model_copy(update={"label": "A named interval"})
    restored = SignalAnnotation.model_validate_json(labeled.to_deterministic_json())
    assert restored.label == "A named interval"
    assert restored.note == annotation.note
    assert restored.attributes == annotation.attributes


def test_bar_paints_annotation_label_and_unlabeled_fallback(qtbot, monkeypatch):
    view = WaveformView()
    qtbot.addWidget(view)
    view.resize(820, 210)
    view.show()
    annotation = SignalAnnotation(
        id=uuid4(),
        concept_ref=ConceptRef("core@0.1:test"),
        label="Named interval",
        geometry=TimeIntervalGeometry(start_sample=100, end_sample=600),
    )
    painted = []
    original_painter = QtGui.QPainter

    class TextRecorder(original_painter):
        def drawText(self, *args):
            painted.append(args[-1])
            return super().drawText(*args)

    monkeypatch.setattr(QtGui, "QPainter", TextRecorder)
    view.set_recording(WaveformEnvelope((0.0,), (0.0,), 800, 8000), (annotation,))
    view.grab()
    assert "Named interval" in painted and "test" not in painted
    painted.clear()
    view.set_annotations(
        (annotation.model_copy(update={"label": None}),), frame_count=800, sample_rate_hz=8000
    )
    view.grab()
    assert "Unlabeled" in painted and "Named interval" not in painted
