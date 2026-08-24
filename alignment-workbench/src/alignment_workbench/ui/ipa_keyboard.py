from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

ITALIAN_IPA_LAYOUT: Mapping[str, Sequence[str]] = {
    "Vowels": ("i", "e", "ɛ", "a", "ɔ", "o", "u"),
    "Consonants": (
        "p",
        "b",
        "t",
        "d",
        "k",
        "g",
        "f",
        "v",
        "s",
        "z",
        "ʃ",
        "ʒ",
        "m",
        "n",
        "ɲ",
        "r",
        "l",
        "ʎ",
        "j",
        "w",
        "t͡s",
        "d͡z",
        "t͡ʃ",
        "d͡ʒ",
    ),
    "Marks": ("ˈ", "ˌ", "ː"),
}


class IpaKeyboard(QtWidgets.QFrame):
    def __init__(
        self,
        target: QtWidgets.QLineEdit,
        layout: Mapping[str, Sequence[str]] = ITALIAN_IPA_LAYOUT,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Popup)
        self.target = target
        self.setObjectName("ipaKeyboard")
        self.setAccessibleName("Italian IPA keyboard")
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)
        for title, symbols in layout.items():
            row = QtWidgets.QHBoxLayout()
            label = QtWidgets.QLabel(title)
            label.setFixedWidth(72)
            row.addWidget(label)
            for symbol in symbols:
                button = QtWidgets.QPushButton(symbol)
                button.setProperty("ipaSymbol", symbol)
                button.setAccessibleName(f"Insert IPA {symbol}")
                button.setFixedSize(31 if len(symbol) == 1 else 42, 27)
                button.clicked.connect(lambda _checked=False, value=symbol: self.insert(value))
                row.addWidget(button)
            row.addStretch(1)
            root.addLayout(row)
        controls = QtWidgets.QHBoxLayout()
        controls.addStretch(1)
        backspace = QtWidgets.QPushButton("Backspace")
        backspace.setObjectName("ipaBackspace")
        backspace.clicked.connect(self.erase)
        clear = QtWidgets.QPushButton("Clear")
        clear.setObjectName("ipaClear")
        clear.clicked.connect(self.clear_target)
        controls.addWidget(backspace)
        controls.addWidget(clear)
        root.addLayout(controls)

    def open_for_target(self) -> None:
        self.adjustSize()
        anchor = self.target.mapToGlobal(QtCore.QPoint(0, self.target.height() + 3))
        screen = self.target.screen().availableGeometry()
        x = min(max(screen.left(), anchor.x()), max(screen.left(), screen.right() - self.width()))
        y = anchor.y()
        if y + self.height() > screen.bottom():
            y = self.target.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.height() - 3
        self.move(x, max(screen.top(), y))
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self.show()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self.isVisible() and event.type() == QtCore.QEvent.Type.MouseButtonPress:
            mouse = event
            global_position = mouse.globalPosition().toPoint()  # type: ignore[attr-defined]
            target_rect = QtCore.QRect(self.target.mapToGlobal(QtCore.QPoint()), self.target.size())
            if not self.frameGeometry().contains(global_position) and not target_rect.contains(
                global_position
            ):
                self.hide()
        elif self.isVisible() and event.type() == QtCore.QEvent.Type.ApplicationDeactivate:
            self.hide()
        return super().eventFilter(watched, event)

    @QtCore.Slot(str)
    def insert(self, symbol: str) -> None:
        start = self.target.selectionStart()
        if start >= 0:
            text = self.target.text()
            length = len(self.target.selectedText())
            self.target.setText(text[:start] + symbol + text[start + length :])
            self.target.setCursorPosition(start + len(symbol))
        else:
            position = self.target.cursorPosition()
            text = self.target.text()
            self.target.setText(text[:position] + symbol + text[position:])
            self.target.setCursorPosition(position + len(symbol))
        self.target.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    @QtCore.Slot()
    def erase(self) -> None:
        if self.target.hasSelectedText():
            self.target.insert("")
        else:
            position = self.target.cursorPosition()
            if position:
                text = self.target.text()
                self.target.setText(text[: position - 1] + text[position:])
                self.target.setCursorPosition(position - 1)
        self.target.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    @QtCore.Slot()
    def clear_target(self) -> None:
        self.target.clear()
        self.target.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.hide()
            self.target.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
            event.accept()
            return
        super().keyPressEvent(event)


class IpaLineEdit(QtWidgets.QLineEdit):
    popup_opened = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.popup = IpaKeyboard(self, parent=self.window())

    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:
        super().focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.show_ipa_keyboard)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        super().mousePressEvent(event)
        QtCore.QTimer.singleShot(0, self.show_ipa_keyboard)

    @QtCore.Slot()
    def show_ipa_keyboard(self) -> None:
        self.popup.open_for_target()
        self.popup_opened.emit()
