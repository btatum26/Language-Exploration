from __future__ import annotations

import threading

import numpy as np

from spectrogram_playground.model.project import Project
from spectrogram_playground.model.transport import TransportState


class Mixer:
    """Pure, callback-safe project mixer driven by one shared frame position."""

    def __init__(self, project: Project, scratch_frames: int = 16_384) -> None:
        self.project = project
        self.lock = threading.RLock()
        self._gain_scratch = np.empty(max(1, scratch_frames), dtype=np.float32)

    def seek(self, frame: int) -> None:
        with self.lock:
            self.project.transport.seek(frame, self.project.total_frames)

    def render(self, frame_count: int) -> np.ndarray:
        """Render exactly frame_count mono frames, honoring an in-buffer loop boundary."""

        output = np.zeros(max(0, int(frame_count)), dtype=np.float32)
        self.render_into(output)
        return output

    def render_into(self, output: np.ndarray) -> None:
        """Fill a backend-owned buffer without allocating an audio-sized array."""

        output.fill(0)
        frame_count = len(output)
        if frame_count <= 0:
            return
        with self.lock:
            transport = self.project.transport
            if transport.state is not TransportState.PLAYING:
                return
            written = 0
            while written < frame_count:
                position = transport.frame_position
                loop = transport.loop_selection and self.project.selection.active
                loop_start = (
                    round(self.project.selection.start * self.project.playback_rate) if loop else 0
                )
                boundary = (
                    round(self.project.selection.end * self.project.playback_rate)
                    if loop
                    else self.project.total_frames
                )
                if boundary <= position:
                    if loop and boundary > loop_start:
                        transport.frame_position = loop_start
                        continue
                    transport.state = TransportState.STOPPED
                    break
                count = min(
                    frame_count - written,
                    boundary - position,
                    len(self._gain_scratch),
                )
                any_solo = any(track.solo and not track.muted for track in self.project.tracks)
                for track in self.project.tracks:
                    audible = not track.muted and (track.solo if any_solo else True)
                    playback_length = track.trim_end_frame - track.trim_start_frame
                    if not audible or position >= playback_length:
                        continue
                    available = min(count, playback_length - position)
                    source_start = track.trim_start_frame + position
                    source = track.playback_samples[source_start : source_start + available]
                    target = output[written : written + available]
                    if track.gain == 1.0:
                        np.add(target, source, out=target)
                    else:
                        scratch = self._gain_scratch[:available]
                        np.multiply(source, track.gain, out=scratch)
                        np.add(target, scratch, out=target)
                written += count
                transport.frame_position += count
                if loop and transport.frame_position >= boundary:
                    transport.frame_position = loop_start
                elif transport.frame_position >= self.project.total_frames:
                    transport.state = TransportState.STOPPED
                    break
        np.clip(output, -1.0, 1.0, out=output)
