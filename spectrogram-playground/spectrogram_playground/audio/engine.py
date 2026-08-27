from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

from spectrogram_playground.model.project import Project
from spectrogram_playground.model.transport import TransportState

from .mixer import Mixer

OutputCallback = Callable[[np.ndarray, float | None], None]


class OutputBackend(Protocol):
    def start(self, callback: OutputCallback, sample_rate: int) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    @property
    def output_clock_time(self) -> float | None: ...


class FakeOutputBackend:
    """Deterministic no-hardware backend used by tests."""

    def __init__(self) -> None:
        self.callback: OutputCallback | None = None
        self.sample_rate = 0
        self.running = False
        self.clock_time: float | None = None

    def start(self, callback: OutputCallback, sample_rate: int) -> None:
        self.callback, self.sample_rate, self.running = callback, sample_rate, True

    def pump(self, frames: int, *, dac_time: float | None = None) -> np.ndarray:
        output = np.zeros(frames, dtype=np.float32)
        if self.running and self.callback is not None:
            self.callback(output, dac_time)
        return output

    @property
    def output_clock_time(self) -> float | None:
        return self.clock_time

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        self.stop()


class SoundDeviceBackend:
    """One PortAudio output stream; importing sounddevice is intentionally lazy."""

    def __init__(self, blocksize: int = 1024) -> None:
        self.blocksize = blocksize
        self.stream: object | None = None
        self.last_error: str | None = None

    def start(self, callback: OutputCallback, sample_rate: int) -> None:
        if self.stream is not None:
            if not self.stream.active:  # type: ignore[attr-defined]
                self.stream.start()  # type: ignore[attr-defined]
            return
        try:
            import sounddevice as sd

            def audio_callback(
                outdata: np.ndarray, _frames: int, _time: object, status: object
            ) -> None:
                if status:
                    self.last_error = str(status)
                callback(outdata[:, 0], float(_time.outputBufferDacTime))

            self.stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                latency="low",
                callback=audio_callback,
            )
            self.stream.start()
        except Exception:
            self.stream = None
            raise

    def stop(self) -> None:
        if self.stream is not None and self.stream.active:  # type: ignore[attr-defined]
            self.stream.abort()  # type: ignore[attr-defined]

    @property
    def output_clock_time(self) -> float | None:
        if self.stream is None:
            return None
        try:
            return float(self.stream.time)  # type: ignore[attr-defined]
        except Exception:
            return None

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class AudioEngine:
    def __init__(self, project: Project, backend: OutputBackend | None = None) -> None:
        self.project = project
        self.mixer = Mixer(project)
        self.backend = backend or SoundDeviceBackend()
        self._dac_anchor_frame: int | None = None
        self._dac_anchor_time: float | None = None

    @property
    def frame_position(self) -> int:
        with self.mixer.lock:
            return self.project.transport.frame_position

    @property
    def current_time(self) -> float:
        return self.audible_frame_position / self.project.playback_rate

    @property
    def audible_frame_position(self) -> float:
        clock_time = self.backend.output_clock_time
        with self.mixer.lock:
            anchor_frame = self._dac_anchor_frame
            anchor_time = self._dac_anchor_time
            if clock_time is None or anchor_frame is None or anchor_time is None:
                return float(self.project.transport.frame_position)
            elapsed = (clock_time - anchor_time) * self.project.playback_rate
            return self._advance_frame(anchor_frame, elapsed)

    def _advance_frame(self, start: int, elapsed: float) -> float:
        maximum = float(self.project.total_frames)
        if self.project.transport.loop_selection and self.project.selection.active:
            rate = self.project.playback_rate
            loop_start = round(self.project.selection.start * rate)
            loop_end = round(self.project.selection.end * rate)
            loop_width = loop_end - loop_start
            if loop_width > 0:
                if loop_start <= start < loop_end:
                    return float(loop_start + ((start - loop_start + elapsed) % loop_width))
                raw = start + elapsed
                if elapsed >= 0 and raw >= loop_end:
                    return float(loop_start + ((raw - loop_end) % loop_width))
                return min(max(0.0, raw), maximum)
        return min(max(0.0, start + elapsed), maximum)

    def _render_into(self, output: np.ndarray, dac_time: float | None) -> None:
        with self.mixer.lock:
            transport = self.project.transport
            was_playing = transport.state is TransportState.PLAYING
            start = transport.frame_position
            if transport.loop_selection and self.project.selection.active:
                loop_end = round(self.project.selection.end * self.project.playback_rate)
                if start >= loop_end:
                    start = round(self.project.selection.start * self.project.playback_rate)
            self.mixer.render_into(output)
            if was_playing and dac_time is not None:
                self._dac_anchor_frame = start
                self._dac_anchor_time = dac_time

    def _clear_timing_anchor(self) -> None:
        self._dac_anchor_frame = None
        self._dac_anchor_time = None

    def play(self) -> None:
        if not self.project.tracks:
            return
        with self.mixer.lock:
            if self.project.transport.frame_position >= self.project.total_frames:
                self.project.transport.frame_position = 0
            self.project.transport.state = TransportState.PLAYING
            self._clear_timing_anchor()
        self.backend.start(self._render_into, self.project.playback_rate)

    def pause(self) -> None:
        frame = round(self.audible_frame_position)
        self.backend.stop()
        with self.mixer.lock:
            self.project.transport.seek(frame, self.project.total_frames)
            self.project.transport.state = TransportState.PAUSED
            self._clear_timing_anchor()

    def stop(self) -> None:
        self.backend.stop()
        with self.mixer.lock:
            self.project.stop()
            self._clear_timing_anchor()

    def seek_seconds(self, seconds: float) -> None:
        was_playing = self.project.transport.is_playing
        if was_playing:
            self.backend.stop()
        self.mixer.seek(round(seconds * self.project.playback_rate))
        with self.mixer.lock:
            self._clear_timing_anchor()
        if was_playing:
            self.backend.start(self._render_into, self.project.playback_rate)

    def close(self) -> None:
        self.stop()
        self.backend.close()
