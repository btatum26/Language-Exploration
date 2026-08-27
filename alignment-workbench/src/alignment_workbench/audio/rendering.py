from __future__ import annotations

import os
import threading
from pathlib import Path
from uuid import UUID, uuid4

import soundfile as sf

from alignment_workbench.state.editor import EditorSession


def render_track_to_wav(session: EditorSession, track_id: UUID, destination: Path) -> Path:
    """Render clip references to a new PCM artifact without touching any source file."""

    snapshot = session.render_snapshot(track_id)
    return write_snapshot_to_wav(snapshot.samples, session.sample_rate, destination)


def write_snapshot_to_wav(
    samples: object,
    sample_rate: int,
    destination: Path,
    cancel: threading.Event | None = None,
) -> Path:
    """Write an immutable snapshot through a sibling temporary file and atomically publish it."""

    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("export was cancelled")
        sf.write(temporary, samples, sample_rate, subtype="PCM_16", format="WAV")
        if cancel is not None and cancel.is_set():
            raise RuntimeError("export was cancelled")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
