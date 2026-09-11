import wave

import numpy as np
import pytest
from PySide6 import QtCore, QtMultimedia

from gui.microphone import MicrophoneDialog
from gui.tasks import TaskRunner
from gui.waveform import load_waveform_envelope
from gui.workstation_window import WorkstationWindow
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_gui import ImmediateRunner


class Device:
    def __init__(self, kind, channels):
        self.format = QtMultimedia.QAudioFormat()
        self.format.setSampleRate(8000)
        self.format.setChannelCount(channels)
        self.format.setSampleFormat(kind)

    def preferredFormat(self):
        return self.format

    def isFormatSupported(self, format):
        return True

    def id(self):
        return b"test-input"

    def description(self):
        return "Test microphone"


@pytest.fixture
def microphone(monkeypatch):
    sources = []

    class Source:
        def __init__(self, *args):
            self.buffer = QtCore.QBuffer()
            self.buffer.open(QtCore.QIODevice.OpenModeFlag.ReadWrite)
            self.failure = QtMultimedia.QtAudio.Error.NoError
            self.stopped = False
            sources.append(self)

        def start(self):
            return self.buffer

        def error(self):
            return self.failure

        def stop(self):
            self.stopped = True

        def deleteLater(self):
            pass

        def feed(self, data):
            self.buffer.close()
            self.buffer.setData(data)
            self.buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)

    def setup(kind=QtMultimedia.QAudioFormat.SampleFormat.Int16, channels=1):
        device = Device(kind, channels)
        monkeypatch.setattr(QtMultimedia.QMediaDevices, "audioInputs", lambda: [device])
        monkeypatch.setattr(QtMultimedia.QMediaDevices, "defaultAudioInput", lambda: device)
        monkeypatch.setattr(QtMultimedia, "QAudioSource", Source)
        return sources

    return setup


@pytest.mark.parametrize("kind,dtype,values,expected", [
    ("UInt8", "u1", [0, 128, 255, 64], [-32768, 0, 32512, -16384]),
    ("Int16", "=i2", [-32768, 0, 32767, 16384], [-32768, 0, 32767, 16384]),
    ("Int32", "=i4", [-2147483648, 0, 1073741824, 0], [-32768, 0, 16384, 0]),
    ("Float", "=f4", [-1, 0, 0.5, 1.1], [-32768, 0, 16384, 32767]),
])
def test_capture_formats_and_split_stereo_frames(
    qtbot, tmp_path, microphone, kind, dtype, values, expected
):
    sources = microphone(getattr(QtMultimedia.QAudioFormat.SampleFormat, kind), 2)
    dialog = MicrophoneDialog(tmp_path / "take.wav")
    qtbot.addWidget(dialog)
    dialog.start()
    data = np.array(values, dtype=dtype).tobytes()
    sources[-1].feed(data[:3])
    dialog._read()
    sources[-1].feed(data[3:])
    dialog.stop()
    assert sources[-1].stopped
    assert dialog.add_button.isEnabled()
    with wave.open(str(dialog.path)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (2, 8000, 2)
        assert np.frombuffer(wav.readframes(2), dtype="<i2").tolist() == expected


def test_empty_device_failure_and_cancel(qtbot, tmp_path, microphone):
    sources = microphone()
    dialog = MicrophoneDialog(tmp_path / "take.wav")
    qtbot.addWidget(dialog)
    dialog.start()
    dialog.stop()
    assert not dialog.add_button.isEnabled()
    dialog.start()
    sources[-1].failure = QtMultimedia.QtAudio.Error.IOError
    dialog._read()
    assert sources[-1].stopped
    assert not dialog.add_button.isEnabled()
    assert "failed" in dialog.status.text()
    dialog.start()
    dialog.reject()
    assert sources[-1].stopped and dialog.writer is None


def test_no_microphone(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QtMultimedia.QMediaDevices, "audioInputs", lambda: [])
    dialog = MicrophoneDialog(tmp_path / "take.wav")
    qtbot.addWidget(dialog)
    assert not dialog.start_button.isEnabled()
    assert not dialog.add_button.isEnabled()
    assert "No microphone" in dialog.status.text()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_add_take_imports_and_reopens(qtbot, tmp_path, microphone, asynchronous):
    sources = microphone()
    api = make_api(MemoryPersistenceStore(), tmp_path)
    runner = TaskRunner() if asynchronous else ImmediateRunner()
    window = WorkstationWindow(api, task_runner=runner)
    qtbot.addWidget(window, before_close_func=lambda w: w.shutdown())
    paths = []

    def record():
        dialog = window.findChild(MicrophoneDialog)
        paths.append(dialog.path)
        dialog.start()
        sources[-1].feed(np.full(8000, 8192, dtype="<i2").tobytes())
        dialog.stop()
        dialog.name.setText("Recorded voice")
        dialog.add_button.click()

    QtCore.QTimer.singleShot(0, record)
    # Prevent a regression from leaving a nested modal loop running forever.
    timeout = QtCore.QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: window.findChild(MicrophoneDialog).reject())
    timeout.start(10000)
    window._record()
    timeout.stop()
    assert len(window.tracks) == 1
    assert not paths[0].exists()
    item = api.list_recordings()[0]
    assert item.display_name == "Recorded voice"
    assert item.duration_seconds == 1
    window.shutdown()
    reopened = api.open_recording(item.recording_id)
    assert reopened.audio.local_path.exists()
    envelope = load_waveform_envelope(reopened.audio.local_path)
    assert envelope is not None
    reopened.close()
    if asynchronous:
        runner.shutdown()
