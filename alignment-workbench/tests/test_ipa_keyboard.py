from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from alignment_workbench.ui.ipa_keyboard import IpaLineEdit


def test_popup_opens_on_focus_and_escape_closes(qtbot) -> None:
    edit = IpaLineEdit()
    qtbot.addWidget(edit)
    edit.show()
    edit.setFocus()
    qtbot.waitUntil(edit.popup.isVisible)
    qtbot.keyClick(edit.popup, QtCore.Qt.Key.Key_Escape)
    assert not edit.popup.isVisible()


def test_symbols_insert_at_caret_replace_selection_and_accumulate(qtbot) -> None:
    edit = IpaLineEdit()
    qtbot.addWidget(edit)
    edit.setText("ae")
    edit.setCursorPosition(1)
    edit.popup.insert("t͡ʃ")
    assert edit.text() == "at͡ʃe"

    edit.setSelection(1, 3)
    edit.popup.insert("ɛ")
    edit.popup.insert("ː")
    assert edit.text() == "aɛːe"


def test_backspace_clear_and_normal_typing(qtbot) -> None:
    edit = IpaLineEdit()
    qtbot.addWidget(edit)
    edit.show()
    qtbot.keyClicks(edit, "abc")
    edit.popup.erase()
    assert edit.text() == "ab"
    edit.popup.clear_target()
    assert edit.text() == ""


def test_clicking_outside_closes_popup(qtbot) -> None:
    window = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(window)
    edit = IpaLineEdit(window)
    outside = QtWidgets.QPushButton("Outside", window)
    layout.addWidget(edit)
    layout.addStretch(1)
    layout.addWidget(outside)
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    edit.setFocus()
    qtbot.waitUntil(edit.popup.isVisible)
    qtbot.mouseClick(outside, QtCore.Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not edit.popup.isVisible())
