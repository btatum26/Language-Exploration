from pathlib import Path

import pytest

from registry_align.config import AppConfig, load_config
from registry_align.errors import ConfigurationError


def test_default_config_is_canonical() -> None:
    config = load_config(None)

    assert config.schema_version == "1"
    assert config.input.entries_path == "entries"
    assert config.input.audio_path == "audio_path"


def test_loads_nested_mapping(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
schema_version = "1"
[input]
entries_path = "lessons.recordings"
audio_path = "audio.path"
transcript_path = "transcript.text"
metadata_paths = ["lesson.id"]
[defaults]
language = "it"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.input.entries_path == "lessons.recordings"
    assert config.input.metadata_paths == ("lesson.id",)
    assert config.defaults.language == "it"


@pytest.mark.parametrize("path", ["recordings[0]", "$.recordings", "audio..path"])
def test_rejects_expression_like_property_paths(tmp_path: Path, path: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'schema_version = "1"\n[input]\nentries_path = "{path}"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="unsafe property path"):
        load_config(config_path)


def test_rejects_unknown_configuration_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('schema_version = "1"\nunknown = true\n', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
        load_config(path)


def test_configuration_schema_is_versioned() -> None:
    schema = AppConfig.model_json_schema()

    assert schema["properties"]["schema_version"]["const"] == "1"


def test_only_entries_path_can_reference_root() -> None:
    with pytest.raises(ValueError, match="must not be empty or '\\$'"):
        AppConfig.model_validate({"input": {"audio_path": "$"}})
