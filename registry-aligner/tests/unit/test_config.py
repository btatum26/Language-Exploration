from pathlib import Path

import pytest

from registry_align.config import AppConfig, database_url, load_config
from registry_align.errors import ConfigurationError


def test_default_config_is_postgresql_v2() -> None:
    config = load_config(None)

    assert config.schema_version == "2"
    assert config.database.ssh_tunnel.host == "registry-db"
    assert config.database.ssh_tunnel.local_port == 5433
    assert config.remote_audio.ssh_host == "registry-db"
    assert config.remote_audio.root == "~/registry-align/audio"
    assert config.remote_audio.batch_size == 24
    assert config.remote_audio.upload_workers == 2
    assert config.artifacts.root == Path(".registry-align-cache")


def test_loads_secret_free_configuration_and_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REGISTRY_ALIGN_DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "REGISTRY_ALIGN_DATABASE_URL=postgresql+psycopg://app:secret@127.0.0.1:5433/db\n",
        encoding="utf-8",
    )
    path = tmp_path / "config.toml"
    path.write_text(
        """
schema_version = "2"
[input]
entries_path = "lessons.recordings"
audio_path = "audio.path"
transcript_path = "transcript.text"
metadata_paths = ["lesson.id"]
[database.ssh_tunnel]
enabled = true
host = "registry-db"
local_port = 5433
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.input.entries_path == "lessons.recordings"
    assert database_url().endswith("@127.0.0.1:5433/db")
    assert "secret" not in config.model_dump_json()


def test_database_url_is_exclusively_environmental(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REGISTRY_ALIGN_DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="REGISTRY_ALIGN_DATABASE_URL is required"):
        database_url()


def test_rejects_non_psycopg_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGISTRY_ALIGN_DATABASE_URL", "sqlite:///registry.db")

    with pytest.raises(ConfigurationError, match=r"postgresql\+psycopg"):
        database_url()


def test_rejects_database_credentials_in_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = "2"\n[database]\nurl = "postgresql://secret"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Extra inputs are not permitted"):
        load_config(path)


def test_configuration_schema_is_versioned() -> None:
    schema = AppConfig.model_json_schema()

    assert schema["properties"]["schema_version"]["const"] == "2"
