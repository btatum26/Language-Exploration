from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np


class RecordingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InputDevice:
    index: int
    name: str
    sample_rate: int
    channels: int

    @property
    def label(self) -> str:
        return f"[{self.index}] {self.name} ({self.sample_rate / 1000:g} kHz, {self.channels} ch)"


class Recorder:
    """Exclusive microphone capture; only one input stream is active at a time."""

    def __init__(self, sample_rate: int = 48_000, sounddevice_module: Any | None = None) -> None:
        self.sample_rate = sample_rate
        self.device: int | None = None
        self.device_name = "System default"
        self._sounddevice_module = sounddevice_module
        self._stream: object | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self._frame_count = 0
        self._peak_level = 0.0

    def _sounddevice(self) -> Any:
        if self._sounddevice_module is None:
            import sounddevice

            self._sounddevice_module = sounddevice
        return self._sounddevice_module

    def available_devices(self) -> list[InputDevice]:
        try:
            devices = self._sounddevice().query_devices()
        except Exception as exc:
            raise RecordingError(f"Could not query microphone devices: {exc}") from exc
        return [
            InputDevice(
                index=index,
                name=str(info["name"]),
                sample_rate=round(float(info["default_samplerate"])),
                channels=int(info["max_input_channels"]),
            )
            for index, info in enumerate(devices)
            if int(info["max_input_channels"]) > 0
        ]

    def select_device(self, device: InputDevice) -> None:
        if self.active:
            raise RecordingError("Cannot change the input device while recording")
        self.device = device.index
        self.device_name = device.name
        self.sample_rate = device.sample_rate

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frame_count

    @property
    def duration(self) -> float:
        return self.frame_count / self.sample_rate

    @property
    def peak_level(self) -> float:
        with self._lock:
            return self._peak_level

    def start(self) -> None:
        if self.active:
            return
        stream: object | None = None
        try:
            sd = self._sounddevice()

            self._chunks = []
            self._frame_count = 0
            self._peak_level = 0.0
            self.last_error = None
            sd.check_input_settings(
                device=self.device,
                channels=1,
                dtype="float32",
                samplerate=self.sample_rate,
            )

            def callback(indata: np.ndarray, _frames: int, _time: object, status: object) -> None:
                if status:
                    self.last_error = str(status)
                channel = indata[:, 0]
                peak = max(abs(float(channel.min(initial=0))), abs(float(channel.max(initial=0))))
                with self._lock:
                    self._chunks.append(channel.copy())
                    self._frame_count += len(channel)
                    self._peak_level = peak

            stream = sd.InputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            self._stream = stream
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            self._stream = None
            raise RecordingError(f"Could not open the microphone: {exc}") from exc

    def stop(self) -> np.ndarray:
        if self._stream is None:
            raise RecordingError("Recording is not active")
        stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            raise RecordingError(f"Could not stop microphone capture cleanly: {exc}") from exc
        with self._lock:
            result = np.concatenate(self._chunks) if self._chunks else np.zeros(1, dtype=np.float32)
            self._chunks = []
            self._peak_level = 0.0
        return result

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                self.last_error = str(exc)
            self._stream = None
