"""Existing-recording browser with durable view state and identity-based selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6 import QtCore, QtMultimedia, QtWidgets

from application import RecordingListItem, Saved
from gui.controller import WorkbenchController
from gui.metadata_editor import MetadataEditor
from gui.recording_discovery import (
    FIELDS,
    editable_metadata,
    filtered,
    grouped,
    selected_once,
    values,
)
from gui.tasks import TaskRunner, TaskSubmitter


class RecordingsBrowser(QtWidgets.QDialog):
    add_requested = QtCore.Signal(object)

    def __init__(
        self,
        catalog: WorkbenchController,
        parent: QtWidgets.QWidget,
        runner: TaskSubmitter | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.runner = runner or TaskRunner(self)
        self.settings = QtCore.QSettings("AlignmentWorkbench", "RecordingBrowser")
        self.recordings: tuple[RecordingListItem, ...] = ()
        self.selected: set[UUID] = set()
        self.filters: dict[str, set[str]] = {field: set() for field in FIELDS}
        self.expansion: dict[str, dict[str, bool]] = {}
        self._rendering = False
        self._saving = False
        self._preview_token = 0
        self.player = QtMultimedia.QMediaPlayer(self)
        self.output = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.state: QtWidgets.QLabel
        self.player.errorOccurred.connect(lambda *_: self.state.setText(self.player.errorString()))
        self.setWindowTitle("Browse existing recordings")
        self.resize(1050, 650)
        layout = QtWidgets.QVBoxLayout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search titles, transcripts, speakers and tags")
        self.search.setAccessibleName("Search recordings")
        layout.addWidget(self.search)
        controls = QtWidgets.QHBoxLayout()
        self.filter_buttons: dict[str, QtWidgets.QToolButton] = {}
        for field, label in FIELDS.items():
            filter_button = QtWidgets.QToolButton()
            filter_button.setText(label)
            filter_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            self.filter_buttons[field] = filter_button
            controls.addWidget(filter_button)
        self.group_by = QtWidgets.QComboBox()
        self.group_by.setAccessibleName("Group by")
        for label, field in [
            ("None", ""),
            ("Speaker", "speakers"),
            ("Language", "languages"),
            ("Collection/session", "collections"),
        ]:
            self.group_by.addItem(label, field)
        controls.addWidget(QtWidgets.QLabel("Group by"))
        controls.addWidget(self.group_by)
        self.sort = QtWidgets.QComboBox()
        self.sort.setAccessibleName("Sort recordings")
        self.sort.addItems(["Recently added", "Recently edited", "Name", "Duration"])
        controls.addWidget(self.sort)
        layout.addLayout(controls)
        self.chips = QtWidgets.QHBoxLayout()
        layout.addLayout(self.chips)
        actions = QtWidgets.QHBoxLayout()
        for label, callback in [
            ("Expand all", lambda: self._expand_all(True)),
            ("Collapse all", lambda: self._expand_all(False)),
            ("Clear filters", self._clear_filters),
            ("Clear selection", self._clear_selection),
        ]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.items = QtWidgets.QTreeWidget()
        self.items.setHeaderLabels(
            ["Title / group", "Speaker", "Language", "Duration", "Added", "Collection/session"]
        )
        self.items.setColumnWidth(0, 300)
        self.items.setExpandsOnDoubleClick(False)
        self.items.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.items.setAccessibleName(
            "Recordings; use checkboxes to select, arrows to expand groups"
        )
        layout.addWidget(self.items, 1)
        self.state = QtWidgets.QLabel()
        self.selection_state = QtWidgets.QLabel()
        self.result_count = QtWidgets.QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.result_count)
        layout.addWidget(self.state)
        layout.addWidget(self.selection_state)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        for label, callback in [
            ("Refresh", catalog.refresh_recordings),
            ("Preview", self._preview),
            ("Stop preview", self._stop_preview),
            ("Details", self._details),
            ("Edit selected metadata", self._edit),
            ("Add selected", self._add),
        ]:
            button = buttons.addButton(label, QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(callback)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.items.itemChanged.connect(self._checked)
        self.items.itemExpanded.connect(lambda item: self._expanded(item, True))
        self.items.itemCollapsed.connect(lambda item: self._expanded(item, False))
        self.items.itemClicked.connect(self._clicked)
        self.items.itemDoubleClicked.connect(self._double_clicked)
        try:
            saved = json.loads(str(self.settings.value("view", "{}")))
            self.search.setText(saved.get("query", ""))
            self.group_by.setCurrentIndex(max(0, self.group_by.findData(saved.get("group", ""))))
            self.sort.setCurrentText(saved.get("sort", "Recently added"))
            self.filters = {key: set(saved.get("filters", {}).get(key, [])) for key in FIELDS}
            self.expansion = saved.get("expansion", {})
        except (ValueError, TypeError, AttributeError):
            pass
        self.search.textChanged.connect(self._render)
        self.group_by.currentIndexChanged.connect(self._render)
        self.sort.currentIndexChanged.connect(self._render)
        catalog.recordings_changed.connect(self.populate)
        catalog.recordings_loading.connect(lambda: self.state.setText("Loading recordings…"))
        catalog.recordings_failed.connect(
            lambda message: self.state.setText(f"{message} — Retry with Refresh.")
        )
        self.finished.connect(self._stop_preview)
        self.populate(catalog.recordings)

    def _remember(self) -> None:
        self.settings.setValue(
            "view",
            json.dumps(
                {
                    "query": self.search.text(),
                    "group": self.group_by.currentData(),
                    "sort": self.sort.currentText(),
                    "filters": {key: sorted(value) for key, value in self.filters.items()},
                    "expansion": self.expansion,
                }
            ),
        )

    def populate(self, recordings: object) -> None:
        if self.state.text().startswith("Loading recordings"):
            self.state.setText("Recordings loaded.")
        self.recordings = cast(tuple[RecordingListItem, ...], recordings)
        for field, button in self.filter_buttons.items():
            menu = QtWidgets.QMenu(button)
            choices = {value for item in self.recordings for value in values(item, field)}
            for value in sorted(choices | self.filters[field]):
                action = menu.addAction(value)
                action.setCheckable(True)
                action.setChecked(value in self.filters[field])
                action.toggled.connect(
                    lambda checked, f=field, v=value: self._toggle(f, v, checked)
                )
            old = button.menu()
            button.setMenu(menu)
            if old:
                old.deleteLater()
        self._render()

    def _toggle(self, field: str, value: str, checked: bool) -> None:
        if checked:
            self.filters[field].add(value)
        else:
            self.filters[field].discard(value)
        self._render()

    def _clear_filters(self) -> None:
        self.filters = {field: set() for field in FIELDS}
        self.search.clear()
        self.populate(self.recordings)

    def _clear_selection(self) -> None:
        self.selected.clear()
        self._render()

    def _render(self) -> None:
        current = self._current()
        highlighted = {
            row.data(0, QtCore.Qt.ItemDataRole.UserRole).recording_id
            for row in self.items.selectedItems()
            if isinstance(row.data(0, QtCore.Qt.ItemDataRole.UserRole), RecordingListItem)
        }
        self._rendering = True
        self.items.clear()
        while self.chips.count():
            layout_item = self.chips.takeAt(0)
            widget = layout_item.widget() if layout_item else None
            if widget:
                widget.deleteLater()
        for field, selected in self.filters.items():
            for value in sorted(selected):
                chip = QtWidgets.QPushButton(f"{FIELDS[field]}: {value} ×")
                chip.clicked.connect(lambda _=False, f=field, v=value: self._remove_filter(f, v))
                self.chips.addWidget(chip)
        results = filtered(
            self.recordings, self.search.text(), self.filters, self.sort.currentText()
        )
        field = str(self.group_by.currentData())
        for label, recordings in grouped(results, field).items():
            parent = self.items.invisibleRootItem()
            if field:
                count = sum(item.recording_id in self.selected for item in recordings)
                parent = QtWidgets.QTreeWidgetItem(
                    [
                        f"{label} — {len(recordings)} recordings"
                        + (f" · {count} selected" if count else "")
                    ]
                )
                parent.setData(0, QtCore.Qt.ItemDataRole.UserRole, label)
                self.items.addTopLevelItem(parent)
            for recording in recordings:
                row = QtWidgets.QTreeWidgetItem(
                    [
                        recording.display_name,
                        ", ".join(values(recording, "speakers")),
                        ", ".join(values(recording, "languages")),
                        f"{recording.duration_seconds:.2f} s",
                        recording.added_at.strftime("%Y-%m-%d") if recording.added_at else "",
                        ", ".join(values(recording, "collections")),
                    ]
                )
                row.setData(0, QtCore.Qt.ItemDataRole.UserRole, recording)
                row.setFlags(row.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    0,
                    QtCore.Qt.CheckState.Checked
                    if recording.recording_id in self.selected
                    else QtCore.Qt.CheckState.Unchecked,
                )
                row.setToolTip(
                    0,
                    f"{recording.recording_id}\nRevision {recording.revision_number}\n"
                    f"Edited {recording.modified_at.isoformat()}\n"
                    f"Audio: {recording.audio_status.value}",
                )
                parent.addChild(row)
                if current and current.recording_id == recording.recording_id:
                    self.items.setCurrentItem(row)
                row.setSelected(recording.recording_id in highlighted)
            if field:
                parent.setExpanded(self.expansion.get(field, {}).get(label, True))
        hidden = self.selected - {item.recording_id for item in results}
        self.result_count.setText(
            f"{len(results)} recordings"
            if results
            else "No recordings match. Clear filters, change your search, or import a recording."
        )
        self.selection_state.setText(
            f"{len(self.selected)} selected"
            + (f" · {len(hidden)} hidden by filters" if hidden else "")
        )
        self._rendering = False
        self._remember()

    def _remove_filter(self, field: str, value: str) -> None:
        self.filters[field].discard(value)
        self.populate(self.recordings)

    def _checked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if self._rendering:
            return
        recording = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(recording, RecordingListItem):
            checked = item.checkState(0) == QtCore.Qt.CheckState.Checked
            # Space toggles every highlighted recording; duplicate appearances share identity.
            rows = self.items.selectedItems() if item.isSelected() else [item]
            for row in rows:
                value = row.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(value, RecordingListItem):
                    if checked:
                        self.selected.add(value.recording_id)
                    else:
                        self.selected.discard(value.recording_id)
            self._sync_selection()

    def _sync_selection(self) -> None:
        self._rendering = True
        visible: set[UUID] = set()
        for index in range(self.items.topLevelItemCount()):
            parent = self.items.topLevelItem(index)
            if parent is None:
                continue
            rows = [parent.child(i) for i in range(parent.childCount())] or [parent]
            count = 0
            for row in rows:
                if row is None:
                    continue
                recording = row.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if isinstance(recording, RecordingListItem):
                    visible.add(recording.recording_id)
                    checked = recording.recording_id in self.selected
                    count += checked
                    row.setCheckState(
                        0,
                        QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked,
                    )
            label = parent.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(label, str):
                parent.setText(
                    0,
                    f"{label} ? {parent.childCount()} recordings"
                    + (f" ? {count} selected" if count else ""),
                )
        hidden = len(self.selected - visible)
        self.selection_state.setText(
            f"{len(self.selected)} selected" + (f" ? {hidden} hidden by filters" if hidden else "")
        )
        self._rendering = False

    def _expanded(self, item: QtWidgets.QTreeWidgetItem, expanded: bool) -> None:
        if not self._rendering:
            label = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            self.expansion.setdefault(str(self.group_by.currentData()), {})[label] = expanded
            self._remember()

    def _clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if isinstance(item.data(0, QtCore.Qt.ItemDataRole.UserRole), str):
            item.setExpanded(not item.isExpanded())

    def _double_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        recording = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(recording, RecordingListItem):
            self.selected.add(recording.recording_id)
            self._add()

    def _expand_all(self, expanded: bool) -> None:
        for index in range(self.items.topLevelItemCount()):
            item = self.items.topLevelItem(index)
            if item:
                item.setExpanded(expanded)

    def _current(self) -> RecordingListItem | None:
        item = self.items.currentItem()
        value = item.data(0, QtCore.Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, RecordingListItem) else None

    def _add(self) -> None:
        if self._saving:
            return
        items = selected_once(self.recordings, self.selected)
        for item in items:
            self.add_requested.emit(item)
        if items:
            self.selected.clear()
            self.accept()

    def _stop_preview(self) -> None:
        self._preview_token += 1
        self.player.stop()

    def _preview(self) -> None:
        item = self._current()
        if not item:
            self.state.setText("Choose a recording row to preview.")
            return
        self._stop_preview()
        token = self._preview_token
        self.state.setText("Loading preview…")

        def load() -> Path:
            session = self.catalog.api.open_recording(item.recording_id)
            try:
                return session.audio.local_path
            finally:
                session.close()

        def play(path: Path) -> None:
            if token == self._preview_token and self.isVisible():
                self.player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
                self.player.play()
                self.state.setText(f"Previewing {item.display_name}")

        self.runner.submit(
            load, play, lambda error: self.state.setText(f"Preview failed: {error}. Retry Preview.")
        )

    def _details(self) -> None:
        item = self._current()
        if item:
            QtWidgets.QMessageBox.information(
                self,
                item.display_name,
                f"Recording: {item.recording_id}\n"
                f"Revision {item.revision_number}: {item.current_revision_id}\n"
                f"Edited: {item.modified_at.isoformat()}\nAudio: {item.audio_status.value}\n"
                f"Tags: {', '.join(item.metadata.tags)}\n"
                f"Varieties: {', '.join(item.metadata.varieties)}\n"
                f"Transcript / annotation labels: {item.transcript}",
            )

    def _edit(self) -> None:
        items = selected_once(self.recordings, self.selected)
        if not items or self._saving:
            self.state.setText("Select recordings using their checkboxes first.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Edit metadata for {len(items)} recordings")
        layout = QtWidgets.QVBoxLayout(dialog)
        editor = MetadataEditor(self.recordings, editable_metadata(items[0]), bulk=len(items) > 1)
        layout.addWidget(editor)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        # Capture UI values before entering the worker; optimistic saves preserve concurrent edits.
        updates = [(item, editor.metadata(editable_metadata(item))) for item in items]
        self._saving = True
        self.state.setText("Saving metadata revisions…")

        def save() -> list[str]:
            failures = []
            for item, metadata in updates:
                try:
                    session = self.catalog.api.open_recording(item.recording_id)
                    try:
                        if session.base_revision_id != item.current_revision_id:
                            raise RuntimeError("Recording changed; refresh and retry")
                        session.set_metadata(metadata)
                        if not isinstance(
                            session.save(message="Edit recording discovery metadata"), Saved
                        ):
                            raise RuntimeError(
                                "Save pending or conflicted; retry pending saves "
                                "before editing again"
                            )
                    finally:
                        session.close(discard_unsaved_changes=True)
                except Exception as error:
                    failures.append(f"{item.display_name}: {error}")
            return failures

        def complete(failures: list[str]) -> None:
            self._saving = False
            self.catalog.refresh_recordings()
            if failures:
                QtWidgets.QMessageBox.warning(
                    self, "Some metadata saves need attention", "\n".join(failures)
                )
            else:
                self.state.setText("Metadata saved.")

        def failed(error: BaseException) -> None:
            self._saving = False
            self.state.setText(f"Save failed: {error}. Retry Edit selected metadata.")

        self.runner.submit(save, complete, failed)

    def reject(self) -> None:
        if self._saving:
            self.state.setText("Saving metadata; wait for completion before closing.")
            return
        super().reject()
