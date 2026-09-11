"""Bounded stereo mixer feeding one Qt output and one device playback clock."""

from __future__ import annotations

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]
from PySide6 import QtCore, QtMultimedia

from gui.workspace import Track, WorkspaceController


def mix_block(tracks: tuple[Track, ...], start: int, count: int, rate: int) -> bytes:
    """Decode only this output block; silence outside clips, linear rate conversion.

    Sources are opened per block so stop/remove never leaves an audio lease open.
    MP3 receives decoder preroll, as in the existing spectral renderer.
    """
    output = np.zeros((count, 2), dtype=np.float32)
    times = (start + np.arange(count)) / rate
    for track in tracks:
        positions = (times - track.offset) * track.sample_rate
        valid = (positions >= 0) & (positions < round(track.duration * track.sample_rate))
        if not valid.any():
            continue
        points = positions[valid]
        first, last = int(points[0]), int(points[-1]) + 2
        with sf.SoundFile(track.path) as source:
            preroll = track.sample_rate // 4 if source.format == "MP3" else 0
            read_start = max(0, first - preroll)
            source.seek(read_start)
            data = source.read(last - read_start, dtype="float32", always_2d=True)
        if not len(data):
            continue
        axis = np.arange(len(data)) + read_start
        for channel in range(2):
            output[valid, channel] += np.interp(
                points, axis, data[:, min(channel, data.shape[1] - 1)]
            )
    return np.clip(output, -1, 1).astype("<f4").tobytes()


class WorkspacePlayer(QtCore.QObject):
    state_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)

    def __init__(self, workspace: WorkspaceController) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.rate = 48_000
        self.sink: QtMultimedia.QAudioSink | None = None
        self.output: QtCore.QIODevice | None = None
        self.playing = False
        self._origin = 0.0
        self._next_frame = 0
        self._pending = b""
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._pump)
        workspace.changed.connect(self._mix_changed)

    def play(self) -> None:
        if self.playing or not self.workspace.tracks:
            return
        device = QtMultimedia.QMediaDevices.defaultAudioOutput()
        fmt = QtMultimedia.QAudioFormat()
        fmt.setSampleRate(self.rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QtMultimedia.QAudioFormat.SampleFormat.Float)
        if device.isNull() or not device.isFormatSupported(fmt):
            self.error.emit("A stereo 48 kHz float audio output is required for playback.")
            return
        if self.workspace.playhead >= self.workspace.duration:
            self.workspace.seek(0)
        self._origin = self.workspace.playhead
        self._next_frame = round(self._origin * self.rate)
        self.sink = QtMultimedia.QAudioSink(device, fmt, self)
        self.sink.setBufferSize(self.rate * 8 // 10)
        self.output = self.sink.start()
        if self.output is None:
            self.error.emit("Could not start audio output.")
            self._release()
            return
        self.playing = True
        self.state_changed.emit(True)
        self.timer.start()
        self._pump()

    def _release(self) -> None:
        self.timer.stop()
        if self.sink is not None:
            self.sink.stop()
            self.sink.deleteLater()
        self.sink = None
        self.output = None
        self._pending = b""
        self.playing = False
        self.state_changed.emit(False)

    def pause(self) -> None:
        if self.sink is not None:
            self.workspace.seek(self._origin + self.sink.processedUSecs() / 1_000_000)
        self._release()

    def stop(self) -> None:
        self._release()
        self.workspace.seek(0)

    def seek(self, seconds: float) -> None:
        playing = self.playing
        self._release()
        self.workspace.seek(seconds)
        if playing:
            self.play()

    def _mix_changed(self) -> None:
        if self.playing:
            self.pause()
            self.play()

    def _pump(self) -> None:
        if self.sink is None or self.output is None:
            return
        if self.sink.error() != QtMultimedia.QtAudio.Error.NoError:
            self.error.emit(f"Audio output failed: {self.sink.error().name}")
            self.pause()
            return
        self.workspace.seek(self._origin + self.sink.processedUSecs() / 1_000_000)
        if self.workspace.playhead >= self.workspace.duration:
            self._release()
            return
        try:
            if not self._pending:
                count = min(2048, self.sink.bytesFree() // 8)
                if count <= 0:
                    return
                self._pending = mix_block(
                    self.workspace.audible_tracks, self._next_frame, count, self.rate
                )
                self._next_frame += count
            written = self.output.write(self._pending)
            if written < 0:
                raise OSError("audio output rejected a block")
            self._pending = self._pending[written:]
        except (OSError, RuntimeError, ValueError) as exc:
            self.error.emit(f"Audio playback failed: {exc}")
            self.pause()
