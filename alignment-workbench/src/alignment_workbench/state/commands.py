from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class Command(Protocol):
    label: str

    def redo(self) -> None: ...

    def undo(self) -> None: ...


@dataclass(slots=True)
class CallbackCommand:
    label: str
    redo_callback: Callable[[], None]
    undo_callback: Callable[[], None]

    def redo(self) -> None:
        self.redo_callback()

    def undo(self) -> None:
        self.undo_callback()


class CommandStack:
    """Small Qt-free command stack used by UI actions and headless tests."""

    def __init__(self, changed: Callable[[], None] | None = None) -> None:
        self._commands: list[Command] = []
        self._index = 0
        self._changed = changed or (lambda: None)

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    @property
    def undo_label(self) -> str:
        return self._commands[self._index - 1].label if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._commands[self._index].label if self.can_redo else ""

    def push(self, command: Command) -> None:
        del self._commands[self._index :]
        command.redo()
        self._commands.append(command)
        self._index += 1
        self._changed()

    def undo(self) -> None:
        if not self.can_undo:
            return
        self._index -= 1
        self._commands[self._index].undo()
        self._changed()

    def redo(self) -> None:
        if not self.can_redo:
            return
        self._commands[self._index].redo()
        self._index += 1
        self._changed()

    def clear(self) -> None:
        self._commands.clear()
        self._index = 0
        self._changed()
