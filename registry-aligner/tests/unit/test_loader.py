from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from registry_align.config import DefaultsConfig, InputConfig
from registry_align.registry.loader import load_registry


class RegistryFactory(Protocol):
    def __call__(self, document: object, audio_paths: tuple[str, ...] = ...) -> Path: ...


def test_loads_canonical_registry_without_changing_sources(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    registry_path = registry_factory(canonical_document)
    registry_before = hashlib.sha256(registry_path.read_bytes()).digest()
    audio_path = registry_path.parent / "audio" / "one.wav"
    audio_before = hashlib.sha256(audio_path.read_bytes()).digest()

    result = load_registry(registry_path)

    assert result.is_valid
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == "lesson-1-luca"
    assert entry.transcript_raw == "L’acqua è già qui."
    assert entry.language == "it"
    assert entry.metadata == {"lesson_id": "lesson-1", "license": "local-test"}
    assert hashlib.sha256(registry_path.read_bytes()).digest() == registry_before
    assert hashlib.sha256(audio_path.read_bytes()).digest() == audio_before


def test_loads_nested_mapping_and_derives_stable_id(registry_factory: RegistryFactory) -> None:
    document = {
        "recordings": [
            {
                "audio": {"path": "audio/one.wav"},
                "transcript": {"text": "Un po' d’acqua", "language": "it"},
                "lesson": {"id": "lesson-2"},
            }
        ]
    }
    registry_path = registry_factory(document)
    mapping = InputConfig(
        entries_path="recordings",
        audio_path="audio.path",
        transcript_path="transcript.text",
        language_path="transcript.language",
        metadata_paths=("lesson.id",),
    )

    first = load_registry(registry_path, mapping=mapping)
    second = load_registry(registry_path, mapping=mapping)

    assert first.is_valid
    assert first.entries[0].id == second.entries[0].id
    assert first.entries[0].id_was_derived
    assert first.entries[0].transcript_raw == "Un po' d’acqua"
    assert first.entries[0].metadata == {"id": "lesson-2"}


def test_derived_id_and_duplicate_path_use_portable_normalization(
    registry_factory: RegistryFactory,
) -> None:
    document = [
        {"audio": "audio\\one.wav", "transcript": "uno"},
        {"audio": "audio/./one.wav", "transcript": "due"},
    ]
    result = load_registry(
        registry_factory(document),
        mapping=InputConfig(entries_path="$", audio_path="audio"),
        defaults=DefaultsConfig(language="it"),
    )

    assert result.entries[0].audio_relative_path == "audio/one.wav"
    assert result.entries[0].id_was_derived
    assert [issue.code for issue in result.issues] == [
        "DUPLICATE_ID",
        "DUPLICATE_AUDIO_PATH",
    ]


def test_supports_root_array_mapping_and_config_default(registry_factory: RegistryFactory) -> None:
    document = [{"audio": "audio/one.wav", "words": "ciao"}]
    registry_path = registry_factory(document)

    result = load_registry(
        registry_path,
        mapping=InputConfig(entries_path="$", audio_path="audio", transcript_path="words"),
        defaults=DefaultsConfig(language="it"),
    )

    assert result.is_valid
    assert result.entries[0].source_entry_index == 0
    assert result.entries[0].language == "it"


def test_canonical_registry_requires_explicit_id(registry_factory: RegistryFactory) -> None:
    document = {
        "schema_version": "1.0",
        "defaults": {"language": "it"},
        "entries": [{"audio_path": "audio/one.wav", "transcript": "ciao"}],
    }

    result = load_registry(registry_factory(document))

    assert not result.is_valid
    assert [issue.code for issue in result.issues] == ["ID_REQUIRED"]


def test_canonical_registry_requires_supported_schema_version(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    canonical_document["schema_version"] = "2.0"

    result = load_registry(registry_factory(canonical_document))

    assert not result.is_valid
    assert result.issues[0].code == "SCHEMA_VERSION_UNSUPPORTED"


def test_reports_all_entry_errors_and_duplicate_values(registry_factory: RegistryFactory) -> None:
    document = {
        "schema_version": "1.0",
        "defaults": {"language": "it"},
        "entries": [
            {"id": "same", "audio_path": "audio/one.wav", "transcript": "uno"},
            {"id": "same", "audio_path": "audio/two.wav", "transcript": "due"},
            {"id": "three", "audio_path": "audio/one.wav", "transcript": "tre"},
            {"id": "bad", "audio_path": "../escape.wav", "transcript": ""},
        ],
    }
    registry_path = registry_factory(document, ("audio/one.wav", "audio/two.wav"))

    result = load_registry(registry_path)

    codes = {issue.code for issue in result.issues}
    assert codes == {
        "DUPLICATE_ID",
        "DUPLICATE_AUDIO_PATH",
        "AUDIO_PATH_TRAVERSAL",
        "TRANSCRIPT_REQUIRED",
    }
    assert len(result.entries) == 1


def test_fail_fast_stops_after_first_bad_entry(registry_factory: RegistryFactory) -> None:
    document = {
        "schema_version": "1.0",
        "entries": [
            {"id": "one", "audio_path": "missing.wav", "transcript": ""},
            {"id": "two", "audio_path": "also-missing.wav", "transcript": ""},
        ],
    }

    result = load_registry(registry_factory(document), fail_fast=True)

    assert result.issues
    assert all(issue.details.get("source_entry_index") == 0 for issue in result.issues)


def test_invalid_json_is_reported_as_read_error(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{", encoding="utf-8")

    from registry_align.errors import RegistryReadError

    try:
        load_registry(path)
    except RegistryReadError as exc:
        assert "invalid UTF-8 JSON" in str(exc)
    else:
        raise AssertionError("RegistryReadError was not raised")


def test_raw_json_is_not_rewritten(registry_factory: RegistryFactory) -> None:
    document = {
        "schema_version": "1.0",
        "defaults": {"language": "it"},
        "entries": [{"id": "one", "audio_path": "audio/one.wav", "transcript": "finí (nota)"}],
    }
    path = registry_factory(document)
    before = path.read_text(encoding="utf-8")

    load_registry(path)

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before) == document
