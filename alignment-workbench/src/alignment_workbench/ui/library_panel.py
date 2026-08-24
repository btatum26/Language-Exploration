from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from alignment_workbench.services.models import CatalogPage, CatalogQuery, RecordingSummary
from alignment_workbench.services.registry import RegistryServices
from alignment_workbench.services.tasks import TaskManager


class LibraryPanel(QtWidgets.QWidget):
    add_recording = QtCore.Signal(str)
    relink_recording = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(
        self,
        services: RegistryServices | None,
        tasks: TaskManager,
        *,
        auto_search: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.tasks = tasks
        self.offset = 0
        self.total = 0
        self._build()
        tasks.completed.connect(self._task_completed)
        tasks.failed.connect(self._task_failed)
        if auto_search:
            self.search()

    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Transcript, speaker, ID, word, or sound")
        self.search_edit.returnPressed.connect(self.search)
        layout.addWidget(self.search_edit)
        filters = QtWidgets.QHBoxLayout()
        self.language = QtWidgets.QComboBox()
        self.language.addItems(("All languages", "it", "en"))
        self.language.currentIndexChanged.connect(self.search)
        self.review = QtWidgets.QComboBox()
        self.review.addItems(
            ("All review states", "unreviewed", "needs-review", "verified", "rejected")
        )
        self.review.currentIndexChanged.connect(self.search)
        filters.addWidget(self.language)
        filters.addWidget(self.review)
        layout.addLayout(filters)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(("Recording", "State"))
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._add_selected)
        layout.addWidget(self.tree, 1)
        controls = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add track")
        add.clicked.connect(self._add_selected)
        retry = QtWidgets.QPushButton("Retry")
        retry.clicked.connect(self.search)
        relink = QtWidgets.QPushButton("Locate audio")
        relink.clicked.connect(self._relink_selected)
        self.more = QtWidgets.QPushButton("Load more")
        self.more.clicked.connect(self.load_more)
        controls.addWidget(add)
        controls.addWidget(retry)
        controls.addWidget(relink)
        controls.addStretch(1)
        controls.addWidget(self.more)
        layout.addLayout(controls)
        self.status = QtWidgets.QLabel("Not connected")
        layout.addWidget(self.status)

    @QtCore.Slot()
    def search(self) -> None:
        self.offset = 0
        self.tree.clear()
        self._request_page(replace=True)

    @QtCore.Slot()
    def load_more(self) -> None:
        if self.offset < self.total:
            self._request_page(replace=False)

    def _request_page(self, *, replace: bool) -> None:
        if self.services is None:
            self.status.setText("Disconnected: backend configuration unavailable")
            return
        query = CatalogQuery(
            text=self.search_edit.text().strip(),
            language=self.language.currentText() if self.language.currentIndex() else None,
            review_state=self.review.currentText() if self.review.currentIndex() else None,
            offset=self.offset,
            limit=50,
        )
        self.status.setText("Querying registry…")
        self.tasks.submit(
            "catalog", lambda _cancel, _progress: self.services.catalog(query), replace=replace
        )

    @QtCore.Slot(str, object, object)
    def _task_completed(self, category: str, _request_id: object, result: object) -> None:
        if category != "catalog" or not isinstance(result, CatalogPage):
            return
        self._append_page(result)

    @QtCore.Slot(str, object, str)
    def _task_failed(self, category: str, _request_id: object, message: str) -> None:
        if category != "catalog":
            return
        self.status.setText(f"Disconnected · {message}")
        self.error.emit(message)

    def _append_page(self, page: CatalogPage) -> None:
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            groups[item.text(0)] = item
        for recording in page.items:
            speaker = recording.speaker_id or "No speaker"
            parent = groups.get(speaker)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem((speaker, ""))
                parent.setExpanded(True)
                self.tree.addTopLevelItem(parent)
                groups[speaker] = parent
            child = QtWidgets.QTreeWidgetItem(
                (
                    self._label(recording),
                    f"{recording.alignment_state} · {recording.review_state}",
                )
            )
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, recording.recording_id)
            child.setToolTip(
                0,
                f"{recording.recording_id}\n{recording.transcript}\n"
                f"Audio: {'local' if recording.audio_available else 'missing locally'}",
            )
            parent.addChild(child)
        self.offset = page.offset + len(page.items)
        self.total = page.total
        self.more.setEnabled(page.has_more)
        self.status.setText(f"{self.offset} of {self.total} recordings")

    @staticmethod
    def _label(recording: RecordingSummary) -> str:
        local = "●" if recording.audio_available else "○"
        transcript = recording.transcript.replace("\n", " ")
        if len(transcript) > 52:
            transcript = transcript[:49] + "…"
        return f"{local} {transcript}  [{recording.language}]"

    def _add_selected(self, *_args: object) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        recording_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if recording_id:
            self.add_recording.emit(str(recording_id))

    def _relink_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        recording_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if recording_id:
            self.relink_recording.emit(str(recording_id))
