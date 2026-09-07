from uuid import uuid4

import pytest
from PySide6 import QtCore

from gui.waveform import WaveformEnvelope, WaveformView
from models import ConceptRef, SignalAnnotation, TimeIntervalGeometry


def interval(start, end):
    return SignalAnnotation(
        id=uuid4(),
        concept_ref=ConceptRef("core@0.1:test"),
        label="Interval",
        geometry=TimeIntervalGeometry(start_sample=start, end_sample=end),
    )


@pytest.fixture
def view(qtbot):
    widget = WaveformView()
    qtbot.addWidget(widget)
    widget.resize(820, 210)
    widget.set_recording(WaveformEnvelope((0.0,), (0.0,), 800, 8_000), (interval(200, 600),))
    widget.show()
    return widget


@pytest.mark.parametrize("spectrogram", [False, True])
@pytest.mark.parametrize("edge,offset,expected", [(200, -9, (240, 600)), (600, 9, (200, 640))])
def test_hover_and_drag_edges_without_selecting_first(
    qtbot, view, spectrogram, edge, offset, expected
):
    view.set_spectrogram_mode(spectrogram)
    commits, selections, seeks = [], [], []
    view.boundary_committed.connect(lambda *args: commits.append(args))
    view.annotation_selected.connect(selections.append)
    view.seek_requested.connect(seeks.append)
    pos = QtCore.QPoint(round(view.sample_to_x(edge)) + offset, 25)
    qtbot.mouseMove(view, pos)
    assert view.cursor().shape() == QtCore.Qt.CursorShape.SizeHorCursor
    assert "Drag to change annotation" in view.toolTip()
    assert commits == selections == seeks == []
    qtbot.mousePress(view, QtCore.Qt.MouseButton.LeftButton, pos=pos)
    qtbot.mouseMove(view, pos + QtCore.QPoint(20, 0))
    assert commits == []
    qtbot.mouseMove(view, pos + QtCore.QPoint(40, 0))
    qtbot.mouseRelease(view, QtCore.Qt.MouseButton.LeftButton, pos=pos + QtCore.QPoint(40, 0))
    assert commits == [(view._annotations[0].id, *expected)]
    assert selections == [view._annotations[0].id]
    assert seeks == [] and view.selection is None
    qtbot.mouseMove(view, QtCore.QPoint(400, 25))
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor


def test_hover_prefers_nearest_edge_and_does_not_resize_clipped_edges(qtbot, view):
    outer, inner = interval(100, 700), interval(150, 695)
    view.set_annotations((outer, inner), frame_count=800, sample_rate_hz=8_000)
    qtbot.mouseMove(view, QtCore.QPoint(710, 25))
    assert view._hovered_edge[0] == outer.id
    view.set_viewport(200, 600)
    qtbot.mouseMove(view, QtCore.QPoint(10, 25))
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
    assert view._hovered_edge is None
    view.fit_recording()
    qtbot.mouseMove(view, QtCore.QPoint(710, 25))
    assert view.cursor().shape() == QtCore.Qt.CursorShape.SizeHorCursor
    view.set_editable(False)
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
    commits = []
    view.boundary_committed.connect(lambda *args: commits.append(args))
    qtbot.mousePress(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(710, 25))
    qtbot.mouseRelease(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(750, 25))
    assert commits == []


def test_small_resize_and_hover_follow_updated_geometry(qtbot, view):
    commits = []
    view.boundary_committed.connect(lambda *args: commits.append(args))
    qtbot.mousePress(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(210, 25))
    qtbot.mouseRelease(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(211, 25))
    assert commits == [(view._annotations[0].id, 201, 600)]
    qtbot.mouseMove(view, QtCore.QPoint(610, 25))
    assert view.cursor().shape() == QtCore.Qt.CursorShape.SizeHorCursor
    view.set_annotations((interval(200, 400),), frame_count=800, sample_rate_hz=8_000)
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
    qtbot.mouseMove(view, QtCore.QPoint(410, 25))
    assert view.cursor().shape() == QtCore.Qt.CursorShape.SizeHorCursor
    view.clear()
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
