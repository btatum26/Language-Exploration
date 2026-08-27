from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np

from alignment_workbench.state.commands import CallbackCommand, CommandStack
from alignment_workbench.state.selection import FrameSelection, FrameViewport


class DisplayMode(StrEnum):
    BOTH = "both"
    WAVE = "wave"
    SPEC = "spec"


class ToolMode(StrEnum):
    SEEK = "seek"
    SELECT = "select"
    MOVE = "move"
    BOUNDARY = "boundary"
    SPLIT = "split"


class SessionEventType(StrEnum):
    BATCH = "batch"
    ACTIVE_TRACK = "active-track"
    VIEWPORT = "viewport"
    PLAYHEAD = "playhead"
    SELECTION = "selection"
    SEGMENT_SELECTION = "segment-selection"
    SEGMENTS = "segments"
    TRACK_CONTENT = "track-content"
    RENDER_READY = "render-ready"
    TRACK_MIX = "track-mix"
    TRACK_LAYOUT = "track-layout"
    TRACK_ORDER = "track-order"
    TRACK_ADDED = "track-added"
    TRACK_REMOVED = "track-removed"
    HISTORY = "history"
    CLIPBOARD = "clipboard"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """A targeted state change consumed by the timeline and audio adapters."""

    reason: SessionEventType
    track_id: UUID | None = None
    changes: frozenset[SessionEventType] = frozenset()


@dataclass(frozen=True, slots=True)
class AudioSource:
    id: UUID
    identity: str
    path: Path | None
    samples: np.ndarray = field(compare=False, repr=False)
    sample_rate: int
    original_rate: int
    channels: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.original_rate <= 0 or self.channels <= 0:
            raise ValueError("Audio source rates and channels must be positive")
        values = np.asarray(self.samples, dtype=np.float32).reshape(-1)
        values.setflags(write=False)
        object.__setattr__(self, "samples", values)

    @property
    def frame_count(self) -> int:
        return len(self.samples)


@dataclass(frozen=True, slots=True)
class Clip:
    source_id: UUID
    source_start_frame: int
    source_end_frame: int
    timeline_start_frame: int
    gain: float = 1.0
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.source_start_frame < 0 or self.source_end_frame <= self.source_start_frame:
            raise ValueError("Clip source bounds are invalid")
        if self.timeline_start_frame < 0:
            raise ValueError("Clip timeline start must be non-negative")
        if self.gain < 0:
            raise ValueError("Clip gain must be non-negative")

    @property
    def frame_count(self) -> int:
        return self.source_end_frame - self.source_start_frame

    @property
    def timeline_end_frame(self) -> int:
        return self.timeline_start_frame + self.frame_count


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    kind: str
    label: str
    start_frame: int
    end_frame: int
    timebase_sample_rate_hz: int = 16_000
    parent_id: str | None = None
    confidence: float | None = None
    review_state: str = "automatic"
    model_label: str | None = None
    model_start_frame: int | None = None
    model_end_frame: int | None = None
    provenance: dict[str, object] = field(default_factory=dict, compare=False)
    effective_revision_id: str | None = None
    model_segment_id: str | None = None
    topology_version: int = 0
    saved_label: str | None = None
    saved_start_frame: int | None = None
    saved_end_frame: int | None = None

    def __post_init__(self) -> None:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Segment bounds are invalid")
        if self.timebase_sample_rate_hz <= 0:
            raise ValueError("Segment timebase must be positive")

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass(slots=True)
class EditorTrack:
    name: str
    clips: list[Clip]
    id: UUID = field(default_factory=uuid4)
    recording_id: str | None = None
    recording_version_id: str | None = None
    speaker_id: str | None = None
    transcript: str = ""
    language: str = "it"
    color: str = "#59a5d8"
    gain: float = 1.0
    muted: bool = False
    solo: bool = False
    visible: bool = True
    display_mode: DisplayMode = DisplayMode.BOTH
    words: list[Segment] = field(default_factory=list)
    phones: list[Segment] = field(default_factory=list)
    alignment_offset_frames: int = 0
    content_version: int = 0

    @property
    def end_frame(self) -> int:
        return max((clip.timeline_end_frame for clip in self.clips), default=0)


@dataclass(frozen=True, slots=True)
class RenderSnapshot:
    track_id: UUID
    content_version: int
    samples: np.ndarray = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class RenderPlan:
    track_id: UUID
    content_version: int
    frame_count: int
    clips: tuple[Clip, ...]
    sources: dict[UUID, AudioSource] = field(compare=False, repr=False)


class EditorSession:
    """Authoritative integer-frame editor state shared by every widget."""

    COLORS = ("#59a5d8", "#e07a5f", "#81b29a", "#f2cc8f", "#c77dff", "#ff8fab")

    def __init__(self, sample_rate: int = 48_000) -> None:
        if sample_rate <= 0:
            raise ValueError("Sample rate must be positive")
        self.sample_rate = sample_rate
        self.sources: dict[UUID, AudioSource] = {}
        self.tracks: list[EditorTrack] = []
        self.active_track_id: UUID | None = None
        self.reference_track_id: UUID | None = None
        self.playhead_frame = 0
        self.selection = FrameSelection()
        self.viewport = FrameViewport(minimum_width=max(1, sample_rate // 100))
        self.tool = ToolMode.SEEK
        self.selected_segment: tuple[UUID, str] | None = None
        self.clipboard: tuple[Clip, ...] = ()
        self.clipboard_duration_frames = 0
        self.unsaved_alignment_edits: dict[tuple[UUID, str], Segment] = {}
        self.timeline_dirty = False
        self._listeners: list[Callable[[SessionEvent], None]] = []
        self._render_snapshots: dict[UUID, RenderSnapshot] = {}
        self.commands = CommandStack(lambda: self._emit(SessionEventType.HISTORY))

    def subscribe(self, callback: Callable[[SessionEvent], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _emit(self, reason: SessionEventType, track_id: UUID | None = None) -> None:
        event = SessionEvent(reason, track_id)
        for callback in tuple(self._listeners):
            callback(event)

    def _emit_batch(self, changes: set[SessionEventType], track_id: UUID | None = None) -> None:
        event = SessionEvent(SessionEventType.BATCH, track_id, frozenset(changes))
        for callback in tuple(self._listeners):
            callback(event)

    @property
    def total_frames(self) -> int:
        return max((track.end_frame for track in self.tracks), default=0)

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.sample_rate

    @property
    def active_track(self) -> EditorTrack | None:
        if self.active_track_id is None:
            return None
        return self.track(self.active_track_id)

    def track(self, track_id: UUID) -> EditorTrack:
        return next(track for track in self.tracks if track.id == track_id)

    def segment(self, track_id: UUID, segment_id: str) -> Segment:
        track = self.track(track_id)
        return next(item for item in (*track.words, *track.phones) if item.id == segment_id)

    def add_audio_track(
        self,
        samples: np.ndarray,
        *,
        name: str,
        identity: str,
        path: Path | None = None,
        original_rate: int | None = None,
        channels: int = 1,
        recording_id: str | None = None,
        recording_version_id: str | None = None,
        speaker_id: str | None = None,
        transcript: str = "",
        language: str = "it",
        words: list[Segment] | None = None,
        phones: list[Segment] | None = None,
    ) -> EditorTrack:
        source = AudioSource(
            id=uuid4(),
            identity=identity,
            path=path,
            samples=samples,
            sample_rate=self.sample_rate,
            original_rate=original_rate or self.sample_rate,
            channels=channels,
        )
        self.sources[source.id] = source
        track = EditorTrack(
            name=name,
            clips=[Clip(source.id, 0, source.frame_count, 0)],
            recording_id=recording_id,
            recording_version_id=recording_version_id,
            speaker_id=speaker_id,
            transcript=transcript,
            language=language,
            color=self.COLORS[len(self.tracks) % len(self.COLORS)],
            words=list(words or []),
            phones=list(phones or []),
        )
        self.tracks.append(track)
        source.samples.setflags(write=False)
        self._render_snapshots[track.id] = RenderSnapshot(
            track.id, track.content_version, source.samples
        )
        self.active_track_id = track.id
        if self.reference_track_id is None:
            self.reference_track_id = track.id
        if len(self.tracks) == 1:
            self.viewport.fit(self.total_frames)
        self._emit(SessionEventType.TRACK_ADDED, track.id)
        return track

    def add_audio_clip(
        self,
        track_id: UUID,
        samples: np.ndarray,
        *,
        identity: str,
        path: Path | None = None,
        original_rate: int | None = None,
        channels: int = 1,
        timeline_start_frame: int | None = None,
    ) -> Clip:
        """Add immutable source audio to an existing track as an undoable clip."""
        track = self.track(track_id)
        source = AudioSource(
            id=uuid4(),
            identity=identity,
            path=path,
            samples=samples,
            sample_rate=self.sample_rate,
            original_rate=original_rate or self.sample_rate,
            channels=channels,
        )
        if source.frame_count == 0:
            raise ValueError("Cannot add an empty audio source")
        self.sources[source.id] = source
        clip = Clip(
            source_id=source.id,
            source_start_frame=0,
            source_end_frame=source.frame_count,
            timeline_start_frame=(
                self.playhead_frame
                if timeline_start_frame is None
                else max(0, int(timeline_start_frame))
            ),
        )
        after = sorted((*track.clips, clip), key=lambda item: item.timeline_start_frame)
        self._push_clip_edit(track, "Add audio clip", after)
        return clip

    def remove_track(self, track_id: UUID) -> None:
        index = next(index for index, item in enumerate(self.tracks) if item.id == track_id)
        self.tracks.pop(index)
        self._render_snapshots.pop(track_id, None)
        changes = {SessionEventType.TRACK_REMOVED}
        if self.active_track_id == track_id:
            self.active_track_id = (
                self.tracks[min(index, len(self.tracks) - 1)].id if self.tracks else None
            )
            changes.add(SessionEventType.ACTIVE_TRACK)
        if self.reference_track_id == track_id:
            self.reference_track_id = self.tracks[0].id if self.tracks else None
            changes.add(SessionEventType.TRACK_LAYOUT)
        self._clamp_timeline()
        self._emit_batch(changes, track_id)

    def reorder_track(self, track_id: UUID, delta: int) -> None:
        old = next(index for index, item in enumerate(self.tracks) if item.id == track_id)
        new = min(max(0, old + int(delta)), len(self.tracks) - 1)
        if old == new:
            return
        before = tuple(track.id for track in self.tracks)
        after = list(before)
        after.insert(new, after.pop(old))

        def apply(order: tuple[UUID, ...] | list[UUID]) -> None:
            lookup = {track.id: track for track in self.tracks}
            self.tracks[:] = [lookup[item] for item in order]
            self.timeline_dirty = True
            self._emit(SessionEventType.TRACK_ORDER, track_id)

        self.commands.push(
            CallbackCommand("Reorder track", lambda: apply(after), lambda: apply(before))
        )

    def set_display_mode(self, track_id: UUID, mode: DisplayMode) -> None:
        track = self.track(track_id)
        if track.display_mode is mode:
            return
        track.display_mode = mode
        self._emit(SessionEventType.TRACK_LAYOUT, track_id)

    def set_track_mix(
        self,
        track_id: UUID,
        *,
        gain: float | None = None,
        muted: bool | None = None,
        solo: bool | None = None,
    ) -> None:
        track = self.track(track_id)
        changed = False
        if gain is not None:
            value = max(0.0, float(gain))
            if track.gain != value:
                track.gain = value
                changed = True
        if muted is not None and track.muted != bool(muted):
            track.muted = bool(muted)
            changed = True
        if solo is not None and track.solo != bool(solo):
            track.solo = bool(solo)
            changed = True
        if changed:
            self._emit(SessionEventType.TRACK_MIX, track_id)

    def set_track_visible(self, track_id: UUID, visible: bool) -> None:
        track = self.track(track_id)
        if track.visible == bool(visible):
            return
        track.visible = bool(visible)
        self._emit(SessionEventType.TRACK_LAYOUT, track_id)

    def set_track_name(self, track_id: UUID, name: str) -> None:
        track = self.track(track_id)
        value = name.strip()
        if not value or track.name == value:
            return
        track.name = value
        self._emit(SessionEventType.TRACK_LAYOUT, track_id)

    def set_reference_track(self, track_id: UUID) -> None:
        self.track(track_id)
        self.reference_track_id = track_id
        self._emit(SessionEventType.TRACK_LAYOUT)

    def set_active_track(self, track_id: UUID | None) -> None:
        if track_id is not None:
            self.track(track_id)
        if self.active_track_id == track_id:
            return
        self.active_track_id = track_id
        self._emit(SessionEventType.ACTIVE_TRACK, track_id)

    def set_viewport(self, start: int, end: int) -> None:
        before = (self.viewport.start, self.viewport.end)
        self.viewport.set(start, end, self.total_frames)
        if (self.viewport.start, self.viewport.end) != before:
            self._emit(SessionEventType.VIEWPORT)

    def fit_viewport(self) -> None:
        before = (self.viewport.start, self.viewport.end)
        self.viewport.fit(self.total_frames)
        if (self.viewport.start, self.viewport.end) != before:
            self._emit(SessionEventType.VIEWPORT)

    def zoom_viewport(self, factor: float, anchor: int) -> None:
        before = (self.viewport.start, self.viewport.end)
        self.viewport.zoom(factor, anchor, self.total_frames)
        if (self.viewport.start, self.viewport.end) != before:
            self._emit(SessionEventType.VIEWPORT)

    def pan_viewport(self, delta: int) -> int:
        before = self.viewport.start
        self.viewport.pan(delta, self.total_frames)
        actual_delta = self.viewport.start - before
        if actual_delta:
            self._emit(SessionEventType.VIEWPORT)
        return actual_delta

    def set_playhead(self, frame: int) -> None:
        value = min(max(0, int(frame)), self.total_frames)
        if value == self.playhead_frame:
            return
        self.playhead_frame = value
        self._emit(SessionEventType.PLAYHEAD)

    def set_selection(self, first: int, second: int) -> None:
        before = (self.selection.start, self.selection.end)
        self.selection.set(first, second, self.total_frames)
        if (self.selection.start, self.selection.end) != before:
            self._emit(SessionEventType.SELECTION)

    def clear_selection(self) -> None:
        if self.selection.start is None and self.selection.end is None:
            return
        self.selection.clear()
        self._emit(SessionEventType.SELECTION)

    def select_segment(self, track_id: UUID, segment_id: str) -> Segment:
        segment = self.segment(track_id, segment_id)
        self.active_track_id = track_id
        self.selected_segment = (track_id, segment_id)
        self.selection.set(segment.start_frame, segment.end_frame, self.total_frames)
        self.playhead_frame = segment.start_frame
        self._emit_batch(
            {
                SessionEventType.ACTIVE_TRACK,
                SessionEventType.SEGMENT_SELECTION,
                SessionEventType.SELECTION,
                SessionEventType.PLAYHEAD,
            },
            track_id,
        )
        return segment

    def update_segment(self, track_id: UUID, updated: Segment, *, unsaved: bool = True) -> None:
        track = self.track(track_id)
        collection = track.words if updated.kind == "word" else track.phones
        index = next(index for index, item in enumerate(collection) if item.id == updated.id)
        collection[index] = updated
        key = (track_id, updated.id)
        if unsaved:
            self.unsaved_alignment_edits[key] = updated
        else:
            self.unsaved_alignment_edits.pop(key, None)
        if self.selected_segment == key:
            self.selection.set(updated.start_frame, updated.end_frame, self.total_frames)
        self._emit(SessionEventType.SEGMENTS, track_id)

    def revert_segment(self, track_id: UUID, segment_id: str) -> None:
        current = self.segment(track_id, segment_id)
        original = replace(
            current,
            label=current.saved_label or current.label,
            start_frame=current.saved_start_frame
            if current.saved_start_frame is not None
            else current.start_frame,
            end_frame=current.saved_end_frame
            if current.saved_end_frame is not None
            else current.end_frame,
        )
        self.update_segment(track_id, original, unsaved=False)

    def render_track(self, track_id: UUID) -> np.ndarray:
        return self.render_snapshot(track_id).samples

    def render_snapshot(self, track_id: UUID) -> RenderSnapshot:
        track = self.track(track_id)
        cached = self._render_snapshots.get(track_id)
        if cached is not None and cached.content_version == track.content_version:
            return cached
        snapshot = self.compose_render_plan(self.capture_render_plan(track_id))
        self._render_snapshots[track_id] = snapshot
        return snapshot

    def prepared_render_snapshot(self, track_id: UUID) -> RenderSnapshot | None:
        track = self.track(track_id)
        snapshot = self._render_snapshots.get(track_id)
        return (
            snapshot
            if snapshot is not None and snapshot.content_version == track.content_version
            else None
        )

    def capture_render_plan(self, track_id: UUID) -> RenderPlan:
        track = self.track(track_id)
        return RenderPlan(
            track.id,
            track.content_version,
            track.end_frame,
            tuple(track.clips),
            {clip.source_id: self.sources[clip.source_id] for clip in track.clips},
        )

    def install_render_snapshot(self, snapshot: RenderSnapshot) -> bool:
        try:
            track = self.track(snapshot.track_id)
        except StopIteration:
            return False
        if track.content_version != snapshot.content_version:
            return False
        self._render_snapshots[track.id] = snapshot
        self._emit(SessionEventType.RENDER_READY, track.id)
        return True

    @staticmethod
    def compose_render_plan(plan: RenderPlan) -> RenderSnapshot:
        frame_count = plan.frame_count
        output = np.zeros(max(1, frame_count), dtype=np.float32)
        for clip in plan.clips:
            source = plan.sources[clip.source_id]
            values = source.samples[clip.source_start_frame : clip.source_end_frame]
            start, end = clip.timeline_start_frame, clip.timeline_end_frame
            if clip.gain == 1.0:
                output[start:end] += values
            else:
                output[start:end] += values * clip.gain
        np.clip(output, -1.0, 1.0, out=output)
        output.setflags(write=False)
        return RenderSnapshot(plan.track_id, plan.content_version, output)

    def split_at(self, frame: int | None = None) -> None:
        track = self._require_active_track()
        target = self.playhead_frame if frame is None else int(frame)
        after: list[Clip] = []
        split = False
        for clip in track.clips:
            if clip.timeline_start_frame < target < clip.timeline_end_frame and not split:
                offset = target - clip.timeline_start_frame
                after.extend(
                    (
                        replace(clip, source_end_frame=clip.source_start_frame + offset),
                        replace(
                            clip,
                            id=uuid4(),
                            source_start_frame=clip.source_start_frame + offset,
                            timeline_start_frame=target,
                        ),
                    )
                )
                split = True
            else:
                after.append(clip)
        if not split:
            raise ValueError("The split position is not inside a clip")
        self._push_clip_edit(track, "Split clip", after)

    def copy_selection(self) -> tuple[Clip, ...]:
        track = self._require_active_track()
        start, end = self._selection_bounds()
        copied: list[Clip] = []
        for clip in track.clips:
            overlap_start = max(start, clip.timeline_start_frame)
            overlap_end = min(end, clip.timeline_end_frame)
            if overlap_end <= overlap_start:
                continue
            source_offset = overlap_start - clip.timeline_start_frame
            copied.append(
                replace(
                    clip,
                    id=uuid4(),
                    source_start_frame=clip.source_start_frame + source_offset,
                    source_end_frame=clip.source_start_frame
                    + source_offset
                    + overlap_end
                    - overlap_start,
                    timeline_start_frame=overlap_start - start,
                )
            )
        if not copied:
            raise ValueError("The selection does not contain audio on the active track")
        self.clipboard = tuple(copied)
        self.clipboard_duration_frames = end - start
        self._emit(SessionEventType.CLIPBOARD)
        return self.clipboard

    def delete_selection(self) -> None:
        track = self._require_active_track()
        start, end = self._selection_bounds()
        after = self._delete_range(track.clips, start, end)
        if not after:
            raise ValueError("Delete the track instead of deleting all of its audio")
        self._push_clip_edit(
            track, "Delete audio", after, after_selection=None, after_playhead=start
        )

    def cut_selection(self) -> None:
        self.copy_selection()
        track = self._require_active_track()
        start, end = self._selection_bounds()
        after = self._delete_range(track.clips, start, end)
        if not after:
            raise ValueError("Delete the track instead of cutting all of its audio")
        self._push_clip_edit(track, "Cut audio", after, after_selection=None, after_playhead=start)

    def paste(self, frame: int | None = None) -> None:
        if not self.clipboard:
            raise ValueError("The clipboard is empty")
        track = self._require_active_track()
        target = self.playhead_frame if frame is None else max(0, int(frame))
        width = self.clipboard_duration_frames
        shifted: list[Clip] = []
        for clip in track.clips:
            if clip.timeline_end_frame <= target:
                shifted.append(clip)
            elif clip.timeline_start_frame >= target:
                shifted.append(
                    replace(clip, timeline_start_frame=clip.timeline_start_frame + width)
                )
            else:
                offset = target - clip.timeline_start_frame
                shifted.append(replace(clip, source_end_frame=clip.source_start_frame + offset))
                shifted.append(
                    replace(
                        clip,
                        id=uuid4(),
                        source_start_frame=clip.source_start_frame + offset,
                        timeline_start_frame=target + width,
                    )
                )
        shifted.extend(
            replace(item, id=uuid4(), timeline_start_frame=target + item.timeline_start_frame)
            for item in self.clipboard
        )
        self._push_clip_edit(
            track,
            "Paste audio",
            sorted(shifted, key=lambda item: item.timeline_start_frame),
            after_selection=(target, target + width),
            after_playhead=target,
        )

    def move_clip(self, clip_id: UUID, delta_frames: int) -> None:
        track = self._require_active_track()
        after = [
            replace(
                clip, timeline_start_frame=max(0, clip.timeline_start_frame + int(delta_frames))
            )
            if clip.id == clip_id
            else clip
            for clip in track.clips
        ]
        if after == track.clips:
            raise ValueError("Clip does not exist on the active track")
        self._push_clip_edit(
            track, "Move clip", sorted(after, key=lambda item: item.timeline_start_frame)
        )

    def _delete_range(self, clips: list[Clip], start: int, end: int) -> list[Clip]:
        width = end - start
        output: list[Clip] = []
        for clip in clips:
            clip_start, clip_end = clip.timeline_start_frame, clip.timeline_end_frame
            if clip_end <= start:
                output.append(clip)
                continue
            if clip_start >= end:
                output.append(replace(clip, timeline_start_frame=clip_start - width))
                continue
            if clip_start < start:
                left_count = start - clip_start
                output.append(replace(clip, source_end_frame=clip.source_start_frame + left_count))
            if clip_end > end:
                source_start = clip.source_end_frame - (clip_end - end)
                output.append(
                    replace(
                        clip,
                        id=uuid4(),
                        source_start_frame=source_start,
                        timeline_start_frame=start,
                    )
                )
        return sorted(output, key=lambda item: item.timeline_start_frame)

    def _push_clip_edit(
        self,
        track: EditorTrack,
        label: str,
        after: list[Clip],
        *,
        after_selection: tuple[int, int] | None | bool = False,
        after_playhead: int | None = None,
    ) -> None:
        before = tuple(track.clips)
        result = tuple(after)
        before_state = self._capture_navigation()
        target_selection = before_state[1] if after_selection is False else after_selection
        after_state = (
            self.playhead_frame if after_playhead is None else int(after_playhead),
            target_selection,
            (self.viewport.start, self.viewport.end),
        )

        def apply(clips: tuple[Clip, ...], state: tuple[object, object, object]) -> None:
            previous = self._capture_navigation()
            track.clips[:] = clips
            track.content_version += 1
            self._render_snapshots.pop(track.id, None)
            self.timeline_dirty = True
            playhead, selection, viewport = state
            self.playhead_frame = int(playhead)
            if selection is None:
                self.selection.clear()
            else:
                first, second = selection  # type: ignore[misc]
                self.selection.set(first, second, max(self.total_frames, int(second)))
            first, second = viewport  # type: ignore[misc]
            self.viewport.set(first, second, self.total_frames)
            self._clamp_timeline()
            current = self._capture_navigation()
            changes = {SessionEventType.TRACK_CONTENT}
            if previous[0] != current[0]:
                changes.add(SessionEventType.PLAYHEAD)
            if previous[1] != current[1]:
                changes.add(SessionEventType.SELECTION)
            if previous[2] != current[2]:
                changes.add(SessionEventType.VIEWPORT)
            self._emit_batch(changes, track.id)

        self.commands.push(
            CallbackCommand(
                label,
                lambda: apply(result, after_state),
                lambda: apply(before, before_state),
            )
        )

    def _capture_navigation(
        self,
    ) -> tuple[int, tuple[int, int] | None, tuple[int, int]]:
        selection = (
            (self.selection.start, self.selection.end)
            if self.selection.start is not None and self.selection.end is not None
            else None
        )
        return self.playhead_frame, selection, (self.viewport.start, self.viewport.end)

    def _selection_bounds(self) -> tuple[int, int]:
        if not self.selection.active:
            raise ValueError("Select a time range first")
        assert self.selection.start is not None and self.selection.end is not None
        return self.selection.start, self.selection.end

    def _require_active_track(self) -> EditorTrack:
        track = self.active_track
        if track is None:
            raise ValueError("No active track")
        return track

    def _clamp_timeline(self) -> None:
        self.playhead_frame = min(self.playhead_frame, self.total_frames)
        self.viewport.set(self.viewport.start, self.viewport.end, self.total_frames)
        if self.selection.active:
            assert self.selection.start is not None and self.selection.end is not None
            self.selection.set(self.selection.start, self.selection.end, self.total_frames)
