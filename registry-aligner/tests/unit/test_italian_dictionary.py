from __future__ import annotations

import json
from pathlib import Path

from registry_align.alignment.mfa.dictionary import build_italian_dictionary
from registry_align.config import AppConfig, DefaultsConfig, InputConfig


class FakeItalianTranscriber:
    def trans_list(self, word: str) -> list[str]:
        assert word == "gnocco"
        return ["ɲ", "o", "kː", "o"]


def test_builds_dictionary_with_alias_and_generated_pronunciation(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "recordings": [
                    {
                        "id": "one",
                        "audio": {"path": "audio.wav"},
                        "transcript": {"text": "finí gnocco", "language": "it"},
                        "speaker_id": "speaker",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    base = tmp_path / "italian.dict"
    base.write_text(
        "fini\tf i n i\nphones\tɲ o kː\n",
        encoding="utf-8",
    )
    output = tmp_path / "generated" / "registry.dict"
    config = AppConfig(
        input=InputConfig(
            entries_path="recordings",
            audio_path="audio.path",
            transcript_path="transcript.text",
            language_path="transcript.language",
        ),
        defaults=DefaultsConfig(language="it"),
    )

    result = build_italian_dictionary(
        registry,
        base,
        output,
        config,
        transcriber=FakeItalianTranscriber(),
    )

    assert result.vocabulary_words == 2
    assert result.added_words == 2
    assert result.added_pronunciations == 2
    assert output.read_text(encoding="utf-8").splitlines()[-2:] == [
        "finí\tf i n i",
        "gnocco\tɲ o kː o",
    ]
