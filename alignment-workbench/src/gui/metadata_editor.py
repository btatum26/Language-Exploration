"""Reusable optional metadata editor shared by imports, takes, and bulk revision edits."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6 import QtCore, QtWidgets

from application import RecordingListItem
from gui.recording_discovery import FIELDS, UNSPECIFIED, values
from models import RecordingMetadata


class MetadataEditor(QtWidgets.QWidget):
    def __init__(
        self,
        recordings: Iterable[RecordingListItem] = (),
        metadata: RecordingMetadata | None = None,
        *,
        bulk: bool = False,
    ) -> None:
        super().__init__()
        metadata = metadata or RecordingMetadata()
        layout = QtWidgets.QFormLayout(self)
        self.fields: dict[str, QtWidgets.QLineEdit] = {}
        self.enabled_fields: dict[str, QtWidgets.QCheckBox] = {}
        records = tuple(recordings)
        for field, label in {**FIELDS, "varieties": "Variety (language: dialect)"}.items():
            row = QtWidgets.QWidget()
            box = QtWidgets.QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            entry = QtWidgets.QLineEdit(", ".join(getattr(metadata, field)))
            entry.setPlaceholderText("Optional; separate multiple values with commas")
            entry.setAccessibleName(label)
            choices = sorted(
                {
                    value
                    for item in records
                    for value in (
                        values(item, field) if field in FIELDS else item.metadata.varieties
                    )
                    if value != UNSPECIFIED
                }
            )
            picker = QtWidgets.QComboBox()
            picker.addItems(["Reuse choice…", *choices])

            def choose(
                index: int, edit: QtWidgets.QLineEdit = entry, combo: QtWidgets.QComboBox = picker
            ) -> None:
                if index:
                    edit.setText(
                        ", ".join(filter(None, [edit.text().strip(), combo.currentText()]))
                    )
                    combo.setCurrentIndex(0)

            picker.currentIndexChanged.connect(choose)
            entry.setCompleter(QtWidgets.QCompleter(choices, entry))
            entry.completer().setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            if bulk:
                toggle = QtWidgets.QCheckBox("Replace")
                toggle.setAccessibleName(f"Replace {label} for selected recordings")
                entry.setEnabled(False)
                picker.setEnabled(False)
                toggle.toggled.connect(entry.setEnabled)
                toggle.toggled.connect(picker.setEnabled)
                self.enabled_fields[field] = toggle
                box.addWidget(toggle)
            box.addWidget(entry, 1)
            box.addWidget(picker)
            layout.addRow(label, row)
            self.fields[field] = entry

    def metadata(self, base: RecordingMetadata | None = None) -> RecordingMetadata:
        data = (base or RecordingMetadata()).model_dump()
        for field, entry in self.fields.items():
            if field not in self.enabled_fields or self.enabled_fields[field].isChecked():
                data[field] = tuple(entry.text().split(","))
        data["legacy_labels"] = False
        return RecordingMetadata.model_validate(data)
