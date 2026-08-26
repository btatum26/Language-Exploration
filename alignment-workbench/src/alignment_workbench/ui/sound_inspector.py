from __future__ import annotations

from dataclasses import replace

from PySide6 import QtCore, QtWidgets

from alignment_workbench.services.models import RevisionRequest
from alignment_workbench.services.registry import RegistryServices
from alignment_workbench.services.tasks import TaskManager
from alignment_workbench.state.editor import (
    EditorSession,
    Segment,
    SessionEvent,
    SessionEventType,
)
from alignment_workbench.ui.ipa_keyboard import IpaLineEdit


class SoundInspector(QtWidgets.QWidget):
    audition_requested = QtCore.Signal(bool)
    compare_requested = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(
        self,
        session: EditorSession,
        services: RegistryServices | None,
        tasks: TaskManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.services = services
        self.tasks = tasks
        self.current: tuple[object, str] | None = None
        self._pending_revision_segments: set[tuple[object, str]] = set()
        self._build()
        session.subscribe(self._state_changed)
        tasks.completed.connect(self._task_completed)
        tasks.failed.connect(self._task_failed)

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        form = QtWidgets.QFormLayout()
        self.label = IpaLineEdit()
        self.label.textEdited.connect(self._label_changed)
        form.addRow("Label", self.label)
        self.parent_word = QtWidgets.QLabel("—")
        form.addRow("Parent word", self.parent_word)
        self.track_label = QtWidgets.QLabel("—")
        self.track_label.setWordWrap(True)
        form.addRow("Track / recording", self.track_label)
        self.start = QtWidgets.QSpinBox()
        self.start.setRange(0, 2_147_483_647)
        self.start.valueChanged.connect(self._bounds_changed)
        form.addRow("Start frame", self.start)
        self.end = QtWidgets.QSpinBox()
        self.end.setRange(1, 2_147_483_647)
        self.end.valueChanged.connect(self._bounds_changed)
        form.addRow("End frame", self.end)
        self.times = QtWidgets.QLabel("—")
        form.addRow("Time / duration", self.times)
        self.confidence = QtWidgets.QLabel("—")
        form.addRow("Confidence", self.confidence)
        self.review = QtWidgets.QLabel("—")
        form.addRow("Review state", self.review)
        self.provenance = QtWidgets.QLabel("—")
        self.provenance.setWordWrap(True)
        form.addRow("Provenance", self.provenance)
        self.comparison = QtWidgets.QLabel("—")
        self.comparison.setWordWrap(True)
        form.addRow("Reference comparison", self.comparison)
        layout.addLayout(form)
        edits = QtWidgets.QGridLayout()
        for index, (label, callback) in enumerate(
            (
                ("Split", self.split),
                ("Merge previous", lambda: self.merge(-1)),
                ("Merge next", lambda: self.merge(1)),
                ("Accept + save", lambda: self.save("accepted")),
                ("Reject + save", lambda: self.save("rejected")),
                ("Needs review", lambda: self.save("needs-review")),
                ("Revert edit", self.revert),
                ("Audition", lambda: self.audition_requested.emit(False)),
                ("Loop", lambda: self.audition_requested.emit(True)),
                ("A/B", self.compare_requested.emit),
            )
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            edits.addWidget(button, index // 2, index % 2)
        layout.addLayout(edits)
        layout.addWidget(QtWidgets.QLabel("Revision history"))
        self.history = QtWidgets.QListWidget()
        self.history.setMinimumHeight(100)
        layout.addWidget(self.history, 1)
        self.status = QtWidgets.QLabel("Select a word or sound segment")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._set_enabled(False)

    def _state_changed(self, event: SessionEvent) -> None:
        if event.reason not in {
            SessionEventType.SEGMENT_SELECTION,
            SessionEventType.SEGMENTS,
        }:
            return
        selection = self.session.selected_segment
        if selection is None:
            self.current = None
            self._set_enabled(False)
            return
        self.current = selection
        track_id, segment_id = selection
        segment = self.session.segment(track_id, segment_id)
        track = self.session.track(track_id)
        self._set_enabled(True)
        blocked = QtCore.QSignalBlocker(self.label)
        self.label.setText(segment.label)
        del blocked
        blocked_start = QtCore.QSignalBlocker(self.start)
        blocked_end = QtCore.QSignalBlocker(self.end)
        self.start.setValue(segment.start_frame)
        self.end.setValue(segment.end_frame)
        del blocked_start, blocked_end
        parent = (
            next((item.label for item in track.words if item.id == segment.parent_id), "—")
            if segment.parent_id
            else "—"
        )
        self.parent_word.setText(parent)
        self.track_label.setText(f"{track.name} / {track.recording_id or 'unsaved'}")
        self._update_times(segment)
        self.confidence.setText("—" if segment.confidence is None else f"{segment.confidence:.3f}")
        self.review.setText(segment.review_state)
        self.provenance.setText(
            ", ".join(f"{key}: {value}" for key, value in segment.provenance.items()) or "—"
        )
        self.comparison.setText(self._comparison_text(track_id, segment))
        self.status.setText(
            "Unsaved edit" if selection in self.session.unsaved_alignment_edits else ""
        )
        self._load_history(segment.id)

    def _comparison_text(self, track_id: object, segment: Segment) -> str:
        reference_id = self.session.reference_track_id
        if reference_id is None or reference_id == track_id:
            return "Select a practice track"
        track = self.session.track(track_id)  # type: ignore[arg-type]
        reference = self.session.track(reference_id)
        source = track.words if segment.kind == "word" else track.phones
        target = reference.words if segment.kind == "word" else reference.phones
        index = next((index for index, item in enumerate(source) if item.id == segment.id), -1)
        if index < 0 or index >= len(target):
            return "No corresponding reference segment"
        other = target[index]
        duration_delta = (
            segment.duration_frames - other.duration_frames
        ) / self.session.sample_rate
        start_delta = (segment.start_frame - other.start_frame) / self.session.sample_rate
        end_delta = (segment.end_frame - other.end_frame) / self.session.sample_rate
        return (
            f"{other.label} · duration Δ {duration_delta:+.4f}s · "
            f"start Δ {start_delta:+.4f}s · end Δ {end_delta:+.4f}s"
        )

    def _set_enabled(self, enabled: bool) -> None:
        for child in self.findChildren(QtWidgets.QWidget):
            if child is not self.status:
                child.setEnabled(enabled)

    def _label_changed(self, text: str) -> None:
        if self.current is None:
            return
        track_id, segment_id = self.current
        segment = self.session.segment(track_id, segment_id)
        self.session.update_segment(track_id, replace(segment, label=text))

    def _bounds_changed(self) -> None:
        if self.current is None or self.start.value() >= self.end.value():
            return
        track_id, segment_id = self.current
        segment = self.session.segment(track_id, segment_id)
        self.session.update_segment(
            track_id,
            replace(segment, start_frame=self.start.value(), end_frame=self.end.value()),
        )

    def _update_times(self, segment: Segment) -> None:
        start = segment.start_frame / self.session.sample_rate
        end = segment.end_frame / self.session.sample_rate
        self.times.setText(f"{start:.6f}s – {end:.6f}s · {end - start:.6f}s")

    def save(self, state: str) -> None:
        if self.current is None or self.services is None:
            self.error.emit("Registry service is unavailable")
            return
        track_id, segment_id = self.current
        segment = self.session.segment(track_id, segment_id)
        rate = segment.timebase_sample_rate_hz
        request = RevisionRequest(
            segment_id=segment.model_segment_id or segment.id,
            base_run_id=str(segment.provenance.get("run_id", "current")),
            start_sample=round(segment.start_frame * rate / self.session.sample_rate),
            end_sample=round(segment.end_frame * rate / self.session.sample_rate),
            label=segment.label,
            review_state=state,
        )
        self.status.setText("Saving immutable revision…")
        self._pending_revision_segments = {self.current}
        self.tasks.submit(
            "revision",
            lambda _cancel, _progress: self.services.save_revision(request),
            replace=True,
        )

    def revert(self) -> None:
        if self.current is not None:
            self.session.revert_segment(*self.current)

    def split(self) -> None:
        if self.current is None:
            return
        if self.services is None:
            self.error.emit("Registry service is unavailable")
            return
        track_id, segment_id = self.current
        segment = self.session.segment(track_id, segment_id)
        frame = self.session.playhead_frame
        if not segment.start_frame < frame < segment.end_frame:
            self.error.emit("Move the playhead inside the selected segment before splitting")
            return
        track = self.session.track(track_id)
        collection = track.words if segment.kind == "word" else track.phones
        index = next(index for index, item in enumerate(collection) if item.id == segment.id)
        source_id = segment.model_segment_id or segment.id
        first = replace(
            segment,
            id=f"{source_id}:split:0:{frame}",
            end_frame=frame,
            label=segment.label,
            model_segment_id=source_id,
        )
        second = replace(
            segment,
            id=f"{source_id}:split:1:{frame}",
            start_frame=frame,
            model_label=segment.model_label or segment.label,
            model_segment_id=source_id,
        )
        collection[index : index + 1] = [first, second]
        self.session.unsaved_alignment_edits[(track_id, first.id)] = first
        self.session.unsaved_alignment_edits[(track_id, second.id)] = second
        self._pending_revision_segments = {(track_id, first.id), (track_id, second.id)}
        self.session.select_segment(track_id, second.id)
        rate = segment.timebase_sample_rate_hz
        request = RevisionRequest(
            segment_id=source_id,
            base_run_id=str(segment.provenance.get("run_id", "current")),
            start_sample=None,
            end_sample=None,
            label=None,
            review_state="accepted",
            operation="split",
            affected_segment_ids=(source_id,),
            replacement_segments=(
                {
                    "segment_id": first.id,
                    "label": first.label,
                    "start_sample": round(first.start_frame * rate / self.session.sample_rate),
                    "end_sample": round(first.end_frame * rate / self.session.sample_rate),
                    "parent_segment_id": first.parent_id,
                },
                {
                    "segment_id": second.id,
                    "label": second.label,
                    "start_sample": round(second.start_frame * rate / self.session.sample_rate),
                    "end_sample": round(second.end_frame * rate / self.session.sample_rate),
                    "parent_segment_id": second.parent_id,
                },
            ),
        )
        self.tasks.submit(
            "revision",
            lambda _cancel, _progress: self.services.save_revision(request),
            replace=True,
        )
        self.status.setText("Saving split revision…")

    def merge(self, delta: int) -> None:
        if self.current is None:
            return
        if self.services is None:
            self.error.emit("Registry service is unavailable")
            return
        track_id, segment_id = self.current
        track = self.session.track(track_id)
        segment = self.session.segment(track_id, segment_id)
        collection = track.words if segment.kind == "word" else track.phones
        index = next(index for index, item in enumerate(collection) if item.id == segment.id)
        other_index = index + delta
        if not 0 <= other_index < len(collection):
            self.error.emit("There is no adjacent compatible segment")
            return
        other = collection[other_index]
        if (
            segment.kind != other.kind
            or (delta < 0 and other.end_frame != segment.start_frame)
            or (delta > 0 and segment.end_frame != other.start_frame)
        ):
            self.error.emit("Segments must be adjacent and in the same tier")
            return
        left, right = (other, segment) if delta < 0 else (segment, other)
        left_source = left.model_segment_id or left.id
        right_source = right.model_segment_id or right.id
        merged = replace(
            left,
            id=f"{left_source}:merge:{right_source}",
            end_frame=right.end_frame,
            label=f"{left.label}{right.label}",
            model_segment_id=left_source,
        )
        first = min(index, other_index)
        collection[first : first + 2] = [merged]
        self.session.unsaved_alignment_edits[(track_id, merged.id)] = merged
        self._pending_revision_segments = {(track_id, merged.id)}
        self.session.select_segment(track_id, merged.id)
        rate = merged.timebase_sample_rate_hz
        request = RevisionRequest(
            segment_id=left_source,
            base_run_id=str(merged.provenance.get("run_id", "current")),
            start_sample=None,
            end_sample=None,
            label=None,
            review_state="accepted",
            operation="merge",
            affected_segment_ids=(left_source, right_source),
            replacement_segments=(
                {
                    "segment_id": merged.id,
                    "label": merged.label,
                    "start_sample": round(merged.start_frame * rate / self.session.sample_rate),
                    "end_sample": round(merged.end_frame * rate / self.session.sample_rate),
                    "parent_segment_id": merged.parent_id,
                },
            ),
        )
        self.tasks.submit(
            "revision",
            lambda _cancel, _progress: self.services.save_revision(request),
            replace=True,
        )
        self.status.setText("Saving merge revision…")

    def _load_history(self, segment_id: str) -> None:
        if self.services is None:
            return
        target_id = (
            self.session.segment(*self.current).model_segment_id
            if self.current is not None
            else None
        )
        self.tasks.submit(
            "revision-history",
            lambda _cancel, _progress: self.services.revision_history(target_id or segment_id),
            replace=True,
        )

    @QtCore.Slot(str, object, object)
    def _task_completed(self, category: str, _request_id: object, result: object) -> None:
        if category == "revision":
            targets = self._pending_revision_segments or ({self.current} if self.current else set())
            accepted = str(result["review_state"]) == "accepted"  # type: ignore[index]
            for target in tuple(targets):
                track_id, segment_id = target
                try:
                    segment = self.session.segment(track_id, segment_id)  # type: ignore[arg-type]
                except StopIteration:
                    continue
                self.session.update_segment(
                    track_id,  # type: ignore[arg-type]
                    replace(
                        segment,
                        review_state=str(result["review_state"]),  # type: ignore[index]
                        saved_label=segment.label if accepted else segment.saved_label,
                        saved_start_frame=(
                            segment.start_frame if accepted else segment.saved_start_frame
                        ),
                        saved_end_frame=(
                            segment.end_frame if accepted else segment.saved_end_frame
                        ),
                    ),
                    unsaved=False,
                )
            self._pending_revision_segments.clear()
            self.status.setText("Revision saved")
        elif category == "revision-history":
            self.history.clear()
            for item in result:  # type: ignore[union-attr]
                when = item.get("created_at", "")
                self.history.addItem(
                    f"{when} · {item.get('review_state', '')} · "
                    f"{item.get('label_override') or 'bounds only'}"
                )

    @QtCore.Slot(str, object, str)
    def _task_failed(self, category: str, _request_id: object, message: str) -> None:
        if category in {"revision", "revision-history"}:
            if category == "revision":
                self._pending_revision_segments.clear()
            self.status.setText(message)
            self.error.emit(message)
