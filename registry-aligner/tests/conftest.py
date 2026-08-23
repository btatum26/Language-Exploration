from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def registry_factory(tmp_path: Path) -> Callable[[object, tuple[str, ...]], Path]:
    def create(document: object, audio_paths: tuple[str, ...] = ("audio/one.wav",)) -> Path:
        for portable_path in audio_paths:
            audio_path = tmp_path.joinpath(*portable_path.split("/"))
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"fixture audio bytes")
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n"
        )
        return registry_path

    return create


@pytest.fixture
def canonical_document() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "defaults": {"language": "it"},
        "entries": [
            {
                "id": "lesson-1-luca",
                "audio_path": "audio/one.wav",
                "transcript": "L’acqua è già qui.",
                "speaker_id": "luca",
                "metadata": {"lesson_id": "lesson-1"},
                "license": "local-test",
            }
        ],
    }
