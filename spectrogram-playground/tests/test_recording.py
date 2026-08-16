from __future__ import annotations

import numpy as np

from src.audio.recording import Recorder


class FakeStream:
    def __init__(self, callback, **_kwargs) -> None:
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        self.callback(np.array([[0.1], [-0.5], [0.25]], dtype=np.float32), 3, None, None)

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakeSoundDevice:
    def __init__(self) -> None:
        self.checked: dict[str, object] | None = None

    def query_devices(self):
        return [
            {"name": "Output only", "default_samplerate": 48_000, "max_input_channels": 0},
            {"name": "Laptop microphone", "default_samplerate": 44_100, "max_input_channels": 2},
        ]

    def check_input_settings(self, **kwargs) -> None:
        self.checked = kwargs

    def InputStream(self, **kwargs):
        return FakeStream(**kwargs)


def test_recorder_selects_native_rate_and_exposes_live_feedback() -> None:
    sounddevice = FakeSoundDevice()
    recorder = Recorder(sounddevice_module=sounddevice)
    devices = recorder.available_devices()
    assert [device.name for device in devices] == ["Laptop microphone"]
    recorder.select_device(devices[0])
    recorder.start()
    assert sounddevice.checked == {
        "device": 1,
        "channels": 1,
        "dtype": "float32",
        "samplerate": 44_100,
    }
    assert recorder.active
    assert recorder.frame_count == 3
    assert recorder.duration == 3 / 44_100
    assert recorder.peak_level == 0.5
    np.testing.assert_allclose(recorder.stop(), [0.1, -0.5, 0.25])
    assert not recorder.active
