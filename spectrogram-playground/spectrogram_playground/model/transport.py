from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TransportState(StrEnum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(slots=True)
class Transport:
    state: TransportState = TransportState.STOPPED
    frame_position: int = 0
    loop_selection: bool = False
    follow_playhead: bool = True

    def seek(self, frame: int, maximum: int) -> None:
        self.frame_position = min(max(0, int(frame)), max(0, int(maximum)))

    @property
    def is_playing(self) -> bool:
        return self.state is TransportState.PLAYING
