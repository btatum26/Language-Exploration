"""Segment correspondence and guarded practice-then-reference A/B playback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from PySide6 import QtCore

from alignment_workbench.audio.engine import SessionAudioEngine
from alignment_workbench.state.editor import EditorSession, EditorTrack, Segment, SessionEventType


def corresponding_segment(
    practice: EditorTrack, reference: EditorTrack, segment: Segment
) -> Segment | None:
    target = reference.words if segment.kind == "word" else reference.phones
    token_ids = _token_ids(segment)
    if token_ids:
        matches = [item for item in target if token_ids & _token_ids(item)]
        if len(matches) == 1:
            return matches[0]
    if segment.kind == "word":
        return _correspond_by_label_occurrence(practice.words, reference.words, segment)
    if segment.parent_id is not None:
        source_parent = next(
            (item for item in practice.words if item.id == segment.parent_id), None
        )
        if source_parent is None:
            return None
        target_parent = corresponding_segment(practice, reference, source_parent)
        if target_parent is None:
            return None
        source_phones = [item for item in practice.phones if item.parent_id == source_parent.id]
        target_phones = [item for item in reference.phones if item.parent_id == target_parent.id]
        return _ordinal_phone(source_phones, target_phones, segment)
    return _ordinal_phone(practice.phones, reference.phones, segment)


def _ordinal_phone(
    source: list[Segment], target: list[Segment], segment: Segment
) -> Segment | None:
    silence = _is_silence(segment)
    source_filtered = [item for item in source if _is_silence(item) == silence]
    target_filtered = [item for item in target if _is_silence(item) == silence]
    try:
        ordinal = next(index for index, item in enumerate(source_filtered) if item.id == segment.id)
    except StopIteration:
        return None
    return target_filtered[ordinal] if ordinal < len(target_filtered) else None


def _correspond_by_label_occurrence(
    source: list[Segment], target: list[Segment], segment: Segment
) -> Segment | None:
    label = _normalized(segment.label)
    source_matches = [item for item in source if _normalized(item.label) == label]
    target_matches = [item for item in target if _normalized(item.label) == label]
    try:
        ordinal = next(index for index, item in enumerate(source_matches) if item.id == segment.id)
    except StopIteration:
        return None
    return target_matches[ordinal] if ordinal < len(target_matches) else None


def _token_ids(segment: Segment) -> set[str]:
    values = segment.provenance.get("source_token_ids") or segment.provenance.get("token_ids") or ()
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _normalized(label: str) -> str:
    return re.sub(r"[^\w]+", "", label.casefold())


def _is_silence(segment: Segment) -> bool:
    return segment.kind == "silence" or _normalized(segment.label) in {
        "",
        "sil",
        "sp",
        "spn",
        "eps",
        "silence",
    }


@dataclass(frozen=True, slots=True)
class ComparisonState:
    active_track_id: UUID | None
    selection: tuple[int, int] | None
    playhead: int
    loop: bool
    playing: bool
    mix: dict[UUID, tuple[float, bool, bool]]


class ABComparisonController(QtCore.QObject):
    """Play practice then reference and restore all authoritative state exactly."""

    def __init__(self, session: EditorSession, audio: SessionAudioEngine) -> None:
        super().__init__()
        self.session = session
        self.audio = audio
        self._generation = 0
        self._timer_generation = 0
        self._state: ComparisonState | None = None
        self._practice: tuple[UUID, Segment] | None = None
        self._reference: tuple[UUID, Segment] | None = None
        self._internal = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._unsubscribe = session.subscribe(self._state_changed)

    def start(
        self,
        practice_track_id: UUID,
        practice_segment: Segment,
        reference_track_id: UUID,
        reference_segment: Segment,
    ) -> None:
        self.cancel()
        self._generation += 1
        self._state = self._capture()
        self._practice = (practice_track_id, practice_segment)
        self._reference = (reference_track_id, reference_segment)
        self._play_interval(practice_track_id, practice_segment)
        self._timer_generation = self._generation
        self._timer.start(self._duration_ms(practice_segment))

    def cancel(self) -> None:
        self._generation += 1
        self._timer.stop()
        state, self._state = self._state, None
        self._practice = self._reference = None
        if state is not None:
            self._restore(state)

    def close(self) -> None:
        self.cancel()
        self._unsubscribe()

    def _advance(self) -> None:
        if (
            self._state is None
            or self._reference is None
            or self._timer_generation != self._generation
        ):
            return
        if self._practice is not None:
            self._practice = None
            track_id, segment = self._reference
            try:
                self.session.track(track_id)
            except StopIteration:
                self.cancel()
                return
            self._play_interval(track_id, segment)
            self._timer_generation = self._generation
            self._timer.start(self._duration_ms(segment))
            return
        self.cancel()

    def _play_interval(self, track_id: UUID, segment: Segment) -> None:
        self._internal = True
        try:
            for track in tuple(self.session.tracks):
                self.session.set_track_mix(
                    track.id,
                    muted=False if track.id == track_id else track.muted,
                    solo=track.id == track_id,
                )
            self.session.set_active_track(track_id)
            self.session.set_selection(segment.start_frame, segment.end_frame)
            self.audio.set_loop(False)
            self.audio.seek(segment.start_frame)
            self.audio.engine.play()
        finally:
            self._internal = False

    def _capture(self) -> ComparisonState:
        selection = (
            (self.session.selection.start, self.session.selection.end)
            if self.session.selection.start is not None and self.session.selection.end is not None
            else None
        )
        return ComparisonState(
            self.session.active_track_id,
            selection,
            self.session.playhead_frame,
            self.audio.project.transport.loop_selection,
            self.audio.is_playing,
            {track.id: (track.gain, track.muted, track.solo) for track in self.session.tracks},
        )

    def _restore(self, state: ComparisonState) -> None:
        self._internal = True
        try:
            self.audio.engine.pause()
            available = {track.id for track in self.session.tracks}
            for track_id, (gain, muted, solo) in state.mix.items():
                if track_id in available:
                    self.session.set_track_mix(track_id, gain=gain, muted=muted, solo=solo)
            active = (
                state.active_track_id
                if state.active_track_id in available
                else next(iter(available), None)
            )
            self.session.set_active_track(active)
            if state.selection is None:
                self.session.clear_selection()
            else:
                self.session.set_selection(*state.selection)
            self.audio.set_loop(state.loop)
            self.audio.seek(state.playhead)
            if state.playing:
                self.audio.engine.play()
        finally:
            self._internal = False

    def _state_changed(self, event: object) -> None:
        if self._state is None or self._internal:
            return
        reason = event.reason  # type: ignore[attr-defined]
        changes = event.changes if reason is SessionEventType.BATCH else {reason}  # type: ignore[attr-defined]
        if changes & {
            SessionEventType.TRACK_REMOVED,
            SessionEventType.SELECTION,
            SessionEventType.SEGMENT_SELECTION,
            SessionEventType.ACTIVE_TRACK,
        }:
            self.cancel()

    def _duration_ms(self, segment: Segment) -> int:
        return max(1, round(segment.duration_frames * 1000 / self.session.sample_rate))
