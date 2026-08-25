from __future__ import annotations

from uuid import UUID

from src.audio.decoding import track_from_samples
from src.audio.engine import AudioEngine, OutputBackend
from src.model.project import Project
from src.model.track import Track

from alignment_workbench.state.editor import EditorSession, SessionEvent


class SessionAudioEngine:
    """Adapter onto Spectrogram Playground's shared-clock mixer and output engine."""

    def __init__(
        self,
        session: EditorSession,
        backend: OutputBackend | None = None,
    ) -> None:
        self.session = session
        self.project = Project(playback_rate=session.sample_rate)
        self.engine = AudioEngine(self.project, backend=backend)
        self._unsubscribe = session.subscribe(self._state_changed)
        self.sync()

    @property
    def current_frame(self) -> int:
        return round(self.engine.audible_frame_position)

    @property
    def is_playing(self) -> bool:
        return self.project.transport.is_playing

    def _state_changed(self, event: SessionEvent) -> None:
        if event.reason == "track-mix" and event.track_id is not None:
            self._sync_track_mix(event.track_id)
        elif event.reason == "track-layout" and event.track_id is not None:
            self._sync_track_layout(event.track_id)
        elif event.reason == "track-order":
            self._sync_track_order()
        elif event.reason == "track-content" and event.track_id is not None:
            self._sync_track_content(event.track_id)
        elif event.reason == "track-added" and event.track_id is not None:
            self._sync_track_added(event.track_id)
        elif event.reason == "track-removed" and event.track_id is not None:
            self._sync_track_removed(event.track_id)
        elif event.reason in {"timeline", "segment"}:
            self._sync_selection()

    def sync(self) -> None:
        was_playing = self.project.transport.is_playing
        if was_playing:
            self.engine.pause()
        frame = self.session.playhead_frame
        tracks = [self._render_project_track(track.id) for track in self.session.tracks]
        with self.engine.mixer.lock:
            self.project.tracks[:] = tracks
            self.project.selections.clear()
            for track in tracks:
                self.project.selection_for(track.id)
            self.project.active_track_id = self.session.active_track_id
        self._sync_selection()
        self.project.transport.seek(frame, self.project.total_frames)
        if was_playing:
            self.engine.play()

    def _render_project_track(self, track_id: UUID) -> Track:
        editor_track = self.session.track(track_id)
        samples = self.session.render_track(editor_track.id)
        track = track_from_samples(
            samples,
            self.session.sample_rate,
            name=editor_track.name,
            project_rate=self.session.sample_rate,
            analysis_rate=16_000,
            channels=1,
            source_path=None,
            origin="alignment-workbench-session",
        )
        track.id = editor_track.id
        track.color = editor_track.color
        track.gain = editor_track.gain
        track.muted = editor_track.muted
        track.solo = editor_track.solo
        track.visible = editor_track.visible
        return track

    def _sync_track_mix(self, track_id: UUID) -> None:
        editor_track = self.session.track(track_id)
        with self.engine.mixer.lock:
            track = self.project.track_by_id(editor_track.id)
            track.gain = editor_track.gain
            track.muted = editor_track.muted
            track.solo = editor_track.solo

    def _sync_track_layout(self, track_id: UUID) -> None:
        editor_track = self.session.track(track_id)
        with self.engine.mixer.lock:
            track = self.project.track_by_id(editor_track.id)
            track.name = editor_track.name
            track.visible = editor_track.visible

    def _sync_track_order(self) -> None:
        with self.engine.mixer.lock:
            lookup = {track.id: track for track in self.project.tracks}
            self.project.tracks[:] = [lookup[track.id] for track in self.session.tracks]

    def _sync_track_content(self, track_id: UUID) -> None:
        replacement = self._render_project_track(track_id)
        with self.engine.mixer.lock:
            index = next(
                index
                for index, track in enumerate(self.project.tracks)
                if track.id == replacement.id
            )
            self.project.tracks[index] = replacement
            self.project.active_track_id = self.session.active_track_id
            self.project.transport.seek(self.session.playhead_frame, self.project.total_frames)
        self._sync_selection()

    def _sync_track_added(self, track_id: UUID) -> None:
        track = self._render_project_track(track_id)
        index = next(index for index, item in enumerate(self.session.tracks) if item.id == track.id)
        with self.engine.mixer.lock:
            self.project.tracks.insert(index, track)
            self.project.selection_for(track.id)
            self.project.active_track_id = self.session.active_track_id
            self.project.transport.seek(self.session.playhead_frame, self.project.total_frames)
        self._sync_selection()

    def _sync_track_removed(self, track_id: UUID) -> None:
        with self.engine.mixer.lock:
            self.project.remove_track(track_id)
            self.project.active_track_id = self.session.active_track_id
            self.project.transport.seek(self.session.playhead_frame, self.project.total_frames)
        self._sync_selection()

    def _sync_selection(self) -> None:
        with self.engine.mixer.lock:
            if self.session.active_track_id is None:
                return
            selection = self.project.selection_for(self.session.active_track_id)
            if not self.session.selection.active:
                selection.clear()
                return
            assert (
                self.session.selection.start is not None and self.session.selection.end is not None
            )
            selection.set(
                self.session.selection.start / self.session.sample_rate,
                self.session.selection.end / self.session.sample_rate,
                self.session.duration_seconds,
            )

    def play_pause(self) -> None:
        if self.project.transport.is_playing:
            self.engine.pause()
            self.session.set_playhead(self.engine.frame_position)
        else:
            self.seek(self.session.playhead_frame)
            self.engine.play()

    def stop(self) -> None:
        self.engine.stop()
        target = self.session.selection.start if self.session.selection.active else 0
        self.session.set_playhead(target or 0)

    def seek(self, frame: int) -> None:
        target = min(max(0, int(frame)), self.session.total_frames)
        self.engine.seek_seconds(target / self.session.sample_rate)
        self.session.set_playhead(target)

    def set_loop(self, enabled: bool) -> None:
        self.project.transport.loop_selection = bool(enabled)

    def close(self) -> None:
        self._unsubscribe()
        self.engine.close()
