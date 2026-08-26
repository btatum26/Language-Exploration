from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from alignment_workbench.state.editor import EditorSession, SessionEventType, ToolMode


class TransportToolbar(QtWidgets.QToolBar):
    play_pause = QtCore.Signal()
    stop = QtCore.Signal()
    record = QtCore.Signal()
    loop_changed = QtCore.Signal(bool)
    tool_changed = QtCore.Signal(object)

    def __init__(self, session: EditorSession, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Transport", parent)
        self.session = session
        self.setMovable(False)
        self._action("⏮", "Jump to start", lambda: session.set_playhead(0), "Home")
        self._action("▶/Ⅱ", "Play/pause", self.play_pause.emit, "Space")
        self._action("■", "Stop", self.stop.emit)
        self._action("●", "Record", self.record.emit, "R")
        loop = self._action("Loop", "Loop selection", None)
        loop.setCheckable(True)
        loop.toggled.connect(self.loop_changed)
        self.addSeparator()
        group = QtGui.QActionGroup(self)
        group.setExclusive(True)
        for text, mode, shortcut in (
            ("Seek", ToolMode.SEEK, "A"),
            ("Select", ToolMode.SELECT, "S"),
            ("Move", ToolMode.MOVE, "M"),
            ("Boundary", ToolMode.BOUNDARY, "B"),
            ("Split", ToolMode.SPLIT, "T"),
        ):
            action = self._action(text, f"{text} tool", None, shortcut)
            action.setCheckable(True)
            action.setChecked(session.tool is mode)
            action.triggered.connect(lambda _checked=False, value=mode: self._tool(value))
            group.addAction(action)
        self.addSeparator()
        self.time = QtWidgets.QLabel("00:00.000 / 00:00.000")
        self.time.setMinimumWidth(180)
        self.addWidget(self.time)
        self.connection = QtWidgets.QLabel("DB: checking…")
        self.connection.setObjectName("connectionStatus")
        self.addWidget(self.connection)

    def _action(
        self,
        text: str,
        tooltip: str,
        callback: object | None,
        shortcut: str | None = None,
    ) -> QtGui.QAction:
        action = self.addAction(text)
        action.setToolTip(tooltip)
        if shortcut:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        if callback is not None:
            action.triggered.connect(callback)  # type: ignore[arg-type]
        return action

    def _tool(self, mode: ToolMode) -> None:
        self.session.tool = mode
        self.session._emit(SessionEventType.TOOL)
        self.tool_changed.emit(mode)

    def update_time(self, frame: int) -> None:
        self.time.setText(f"{self._format(frame)} / {self._format(self.session.total_frames)}")

    def set_shortcuts_enabled(self, enabled: bool) -> None:
        for action in self.actions():
            if not action.shortcut().isEmpty():
                action.setEnabled(enabled)

    def _format(self, frame: int) -> str:
        seconds = frame / self.session.sample_rate
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes):02d}:{remainder:06.3f}"
