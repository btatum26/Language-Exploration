from __future__ import annotations

from pathlib import Path
from uuid import UUID

import soundfile as sf

from alignment_workbench.state.editor import EditorSession


def render_track_to_wav(session: EditorSession, track_id: UUID, destination: Path) -> Path:
    """Render clip references to a new PCM artifact without touching any source file."""

    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, session.render_track(track_id), session.sample_rate, subtype="PCM_16")
    return destination
