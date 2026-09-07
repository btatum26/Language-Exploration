from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore

from gui.main_window import MainWindow
from gui.spectrogram import load_spectrogram
from tests.test_first_use_gui import drag
from tests.test_gui import DeferredRunner, ImmediateRunner
from tests.test_gui import gui_objects as audio_fixture  # noqa: F401


@pytest.mark.parametrize("extension", ["wav", "mp3"])
def test_spectrogram_resolves_tone_and_viewport(tmp_path, extension):
    rate = 8_000
    tone = 0.5 * np.sin(2 * np.pi * 1_000 * np.arange(rate) / rate)
    path = tmp_path / f"tone.{extension}"
    sf.write(path, tone, rate)
    preview = load_spectrogram(path, 2_000, 6_000, maximum_columns=32)
    assert (preview.start_sample, preview.end_sample) == (2_000, 6_000)
    assert preview.maximum_frequency_hz == 4_000
    assert preview.image.width() == 32
    column = [preview.image.pixelIndex(16, y) for y in range(preview.image.height())]
    peak_hz = (1 - np.argmax(column) / (len(column) - 1)) * 4_000
    assert abs(peak_hz - 1_000) <= 40
    assert max(column) > 200


@pytest.mark.parametrize("rate", [16_000, 44_100, 48_000])
def test_spectrogram_caps_frequency_without_rescaling_tones(tmp_path, rate):
    path = tmp_path / "high-rate.wav"
    samples = 0.5 * np.sin(2 * np.pi * 6_000 * np.arange(rate // 10) / rate)
    sf.write(path, samples, rate)
    preview = load_spectrogram(path, 0, len(samples))
    assert preview.maximum_frequency_hz == 8_000
    column = [preview.image.pixelIndex(5, y) for y in range(preview.image.height())]
    peak_hz = (1 - np.argmax(column) / (len(column) - 1)) * 8_000
    assert abs(peak_hz - 6_000) <= 50
    if rate > 24_000:
        sf.write(path, 0.5 * np.sin(2 * np.pi * 12_000 * np.arange(rate // 10) / rate), rate)
        preview = load_spectrogram(path, 0, len(samples))
        assert max(preview.image.pixelIndex(5, y) for y in range(preview.image.height())) < 30


def test_spectrogram_silence_short_clip_and_opposite_phase_stereo(tmp_path):
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros(3), 8_000)
    preview = load_spectrogram(path, 0, 3)
    assert preview.image.width() == 1
    assert all(preview.image.pixelIndex(0, y) == 0 for y in range(preview.image.height()))
    tone = 0.5 * np.sin(2 * np.pi * 1_000 * np.arange(800) / 8_000)
    sf.write(path, np.stack([tone, -tone], axis=1), 8_000)
    preview = load_spectrogram(path, 0, 800)
    assert max(preview.image.pixelIndex(5, y) for y in range(preview.image.height())) > 200


def test_toggle_preserves_editor_state_and_refreshes_zoom(qtbot, audio_fixture):  # noqa: F811
    api, session = audio_fixture
    tone = 0.5 * np.sin(2 * np.pi * 1_000 * np.arange(800) / 8_000)
    sf.write(session.audio.local_path, tone, 8_000)
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.resize(1024, 768)
    window.show()
    try:
        window.recordings_list.setCurrentRow(0)
        window.open_action.trigger()
        qtbot.waitUntil(lambda: window.waveform.audio_ready)
        window.waveform.set_viewport(100, 700)
        drag(window.waveform, 200, 400)
        window.waveform.set_playhead_seconds(0.05)
        selection = window.waveform.selection
        source = window._player.source()
        dirty = session.dirty
        qtbot.mouseClick(window.audio_view_toggle, QtCore.Qt.MouseButton.LeftButton)
        qtbot.keyClick(window.audio_view_toggle, QtCore.Qt.Key.Key_Down)
        qtbot.keyClick(window.audio_view_toggle, QtCore.Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: window.waveform.spectrogram is not None)
        assert window.waveform.show_spectrogram
        assert window.waveform.viewport == (100, 700)
        assert window.waveform.selection == selection
        assert window.waveform._playhead_seconds == 0.05
        assert window._player.source() == source
        assert session.dirty == dirty
        drag(window.waveform, 300, 500)
        assert window.waveform.selection != selection
        window.waveform.set_viewport(200, 600)
        qtbot.waitUntil(lambda: window.waveform.spectrogram.start_sample == 200)
        assert window.waveform.spectrogram.end_sample == 600
        artifacts = Path(".artifacts/spectrogram")
        artifacts.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(artifacts / "toggle.png"))
        preview = window.waveform.spectrogram
        window.audio_view_toggle.setCurrentIndex(0)
        assert not window.waveform.show_spectrogram
        assert window.waveform.viewport == (200, 600)
        window.audio_view_toggle.setCurrentIndex(1)
        qtbot.wait(160)
        assert window.waveform.spectrogram is preview
    finally:
        window.shutdown()


def test_stale_spectrogram_is_discarded_and_failure_can_retry(qtbot, audio_fixture):  # noqa: F811
    api, _session = audio_fixture
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    try:
        window.recordings_list.setCurrentRow(0)
        window.open_action.trigger()
        qtbot.waitUntil(lambda: window.waveform.audio_ready)
        runner = DeferredRunner()
        original_submit = window._waveform_runner.submit
        window._waveform_runner.submit = runner.submit
        window.audio_view_toggle.setCurrentIndex(1)
        window._detail_timer.stop()
        window._load_spectrogram()
        window.waveform.set_viewport(100, 500)
        runner.complete(0)
        assert window.waveform.spectrogram is None
        window._detail_timer.stop()
        window._load_spectrogram()
        _operation, _success, failure = runner.tasks.pop(0)
        failure(ValueError("decode failed"))
        assert "unavailable" in window.waveform._spectrogram_message
        assert window.waveform.audio_ready
        window._load_spectrogram()
        runner.complete(0)
        assert window.waveform.spectrogram.start_sample == 100
        window.waveform.set_viewport(200, 600)
        window._load_spectrogram()
        window._clear_workspace()
        runner.complete(0)
        assert window.waveform.spectrogram is None
        window._waveform_runner.submit = original_submit
    finally:
        window.shutdown()
