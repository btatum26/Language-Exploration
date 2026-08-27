from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from spectrogram_playground.model.track import Track


class TrackHeader(QtWidgets.QFrame):
    changed = QtCore.Signal()
    activated = QtCore.Signal(object)
    move_requested = QtCore.Signal(object, int)
    remove_requested = QtCore.Signal(object)

    def __init__(self, track: Track, active: bool, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.track = track
        self.setFixedWidth(230)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.set_active(active)
        self._build_layout()

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(
            f"TrackHeader {{ border-left: 4px solid {self.track.color}; }}"
            + ("TrackHeader { background: #293946; }" if active else "")
        )

    def _build_layout(self) -> None:
        track = self.track
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(3)

        top = QtWidgets.QHBoxLayout()
        self.name_edit = QtWidgets.QLineEdit(track.name)
        self.name_edit.setAccessibleName("Track name")
        self.name_edit.editingFinished.connect(self._rename)
        active_button = QtWidgets.QToolButton(text="â—")
        active_button.setToolTip("Make this the active track")
        active_button.setStyleSheet(f"color: {track.color}")
        active_button.clicked.connect(lambda: self.activated.emit(track.id))
        top.addWidget(active_button)
        top.addWidget(self.name_edit, 1)
        layout.addLayout(top)

        controls = QtWidgets.QHBoxLayout()
        self.mute = self._toggle("M", "Mute", track.muted, lambda value: self._set("muted", value))
        self.solo = self._toggle("S", "Solo", track.solo, lambda value: self._set("solo", value))
        self.visible = self._toggle(
            "V", "Visible", track.visible, lambda value: self._set("visible", value)
        )
        up = QtWidgets.QToolButton(text="â†‘")
        down = QtWidgets.QToolButton(text="â†“")
        remove = QtWidgets.QToolButton(text="Ã—")
        up.clicked.connect(lambda: self.move_requested.emit(track.id, -1))
        down.clicked.connect(lambda: self.move_requested.emit(track.id, 1))
        remove.clicked.connect(lambda: self.remove_requested.emit(track.id))
        for widget in (self.mute, self.solo, self.visible, up, down, remove):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)

        gain_row = QtWidgets.QHBoxLayout()
        gain_row.addWidget(QtWidgets.QLabel("Gain"))
        gain = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        gain.setRange(0, 200)
        gain.setValue(round(track.gain * 100))
        gain.setToolTip("Track playback gain")
        gain.valueChanged.connect(lambda value: self._set("gain", value / 100, emit=False))
        gain.sliderReleased.connect(self.changed.emit)
        gain_row.addWidget(gain)
        layout.addLayout(gain_row)
        scale_row = QtWidgets.QHBoxLayout()
        scale_row.addWidget(QtWidgets.QLabel("View"))
        amplitude = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        amplitude.setRange(25, 400)
        amplitude.setValue(round(track.amplitude_scale * 100))
        amplitude.setToolTip("Waveform vertical amplitude scale")
        amplitude.valueChanged.connect(
            lambda value: self._set("amplitude_scale", value / 100, emit=False)
        )
        amplitude.sliderReleased.connect(self.changed.emit)
        scale_row.addWidget(amplitude)
        layout.addLayout(scale_row)
        metadata = QtWidgets.QLabel(track.metadata_summary)
        metadata.setStyleSheet("color: #aeb4bc; font-size: 8pt")
        layout.addWidget(metadata)

    def _toggle(self, text: str, tip: str, checked: bool, slot: object) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton(text=text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setToolTip(tip)
        button.toggled.connect(slot)  # type: ignore[arg-type]
        return button

    def _set(self, attribute: str, value: object, *, emit: bool = True) -> None:
        setattr(self.track, attribute, value)
        if emit:
            self.changed.emit()

    def _rename(self) -> None:
        name = self.name_edit.text().strip()
        self.track.name = name or "Untitled track"
        self.name_edit.setText(self.track.name)
        self.changed.emit()
