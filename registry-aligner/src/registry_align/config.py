"""Strict, secret-free application configuration."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from registry_align.errors import ConfigurationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputConfig(StrictModel):
    entries_path: str = "entries"
    id_path: str = "id"
    audio_path: str = "audio_path"
    transcript_path: str = "transcript"
    language_path: str = "language"
    speaker_path: str = "speaker_id"
    metadata_paths: tuple[str, ...] = ()

    @field_validator("entries_path")
    @classmethod
    def validate_entries_path(cls, value: str) -> str:
        from registry_align.registry.mapping import validate_property_path

        validate_property_path(value, allow_root=value == "$")
        return value

    @field_validator("id_path", "audio_path", "transcript_path", "language_path", "speaker_path")
    @classmethod
    def validate_entry_path(cls, value: str) -> str:
        from registry_align.registry.mapping import validate_property_path

        validate_property_path(value)
        return value

    @field_validator("metadata_paths")
    @classmethod
    def validate_metadata_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        from registry_align.registry.mapping import validate_property_path

        keys: set[str] = set()
        for value in values:
            validate_property_path(value)
            key = value.rsplit(".", 1)[-1]
            if key in keys:
                raise ValueError(f"metadata paths produce duplicate key {key!r}")
            keys.add(key)
        return values


class DefaultsConfig(StrictModel):
    language: str | None = None


class SourceConfig(StrictModel):
    corpus_id: str = "default"
    root: Path = Field(default_factory=lambda: Path("."))


class AudioConfig(StrictModel):
    editor_pcm_mode: Literal["native"] = "native"
    alignment_sample_rate_hz: int = Field(default=16000, gt=0)
    alignment_channels: int = Field(default=1, gt=0)
    ffmpeg_executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"
    preparation_version: str = "1"


class ArtifactConfig(StrictModel):
    root: Path = Field(default_factory=lambda: Path(".registry-align-cache"))


class RemoteAudioConfig(StrictModel):
    ssh_host: str = "registry-db"
    ssh_executable: str = "ssh"
    scp_executable: str = "scp"
    root: str = "~/registry-align/audio"
    operation_timeout_seconds: int = Field(default=300, ge=10)
    batch_size: int = Field(default=24, ge=1, le=128)
    upload_workers: int = Field(default=2, ge=1, le=8)

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        if not value.startswith("~/"):
            raise ValueError("remote audio root must be relative to the SSH user's home (`~/...`)")
        if any(character.isspace() for character in value) or any(
            character in value for character in "'\"`$;|&<>"
        ):
            raise ValueError("remote audio root contains unsupported shell characters")
        return value.rstrip("/")


class TextConfig(StrictModel):
    normalizer: str = "conservative"
    unicode_form: Literal["NFC"] = "NFC"
    lowercase: bool = True


class MfaConfig(StrictModel):
    executable: str = "mfa"
    config_path: str = ""
    model_mode: Literal["auto", "legacy", "hosted"] = "auto"
    acoustic_model: str = ""
    dictionary: str = ""
    g2p_model: str = ""
    use_g2p_for_oov: bool = False
    fine_tune: bool = False
    validate_corpus: bool = True
    dialect: str = ""


class AlignmentConfig(StrictModel):
    backend: str = "mfa"
    jobs: int = Field(default=1, gt=0)
    continue_on_error: bool = True
    pipeline_version: str = "1"
    claim_lease_seconds: int = Field(default=3600, ge=60)
    mfa: MfaConfig = Field(default_factory=MfaConfig)


class SshTunnelConfig(StrictModel):
    enabled: bool = True
    host: str = "registry-db"
    executable: str = "ssh"
    local_host: str = "127.0.0.1"
    local_port: int = Field(default=5433, ge=1, le=65535)
    startup_timeout_seconds: float = Field(default=5.0, gt=0)


class DatabaseConfig(StrictModel):
    pool_size: int = Field(default=3, ge=1, le=20)
    max_overflow: int = Field(default=1, ge=0, le=20)
    pool_timeout_seconds: int = Field(default=10, ge=1)
    connect_timeout_seconds: int = Field(default=10, ge=1)
    statement_timeout_ms: int = Field(default=30000, ge=1000)
    ssh_tunnel: SshTunnelConfig = Field(default_factory=SshTunnelConfig)


class RetentionConfig(StrictModel):
    prune_runs: bool = True
    run_metadata_days: int = Field(default=30, ge=1)
    minimum_runs_to_keep: int = Field(default=10, ge=0)
    failed_run_days: int = Field(default=14, ge=1)


class OutputConfig(StrictModel):
    directory: Path = Field(default_factory=lambda: Path("exports"))
    formats: tuple[Literal["jsonl", "csv", "textgrid"], ...] = ("jsonl",)


class AppConfig(StrictModel):
    schema_version: Literal["2"] = "2"
    input: InputConfig = Field(default_factory=InputConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)
    remote_audio: RemoteAudioConfig = Field(default_factory=RemoteAudioConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def database_url() -> str:
    return _postgres_url("REGISTRY_ALIGN_DATABASE_URL", required=True)


def database_admin_url() -> str:
    return _postgres_url("REGISTRY_ALIGN_DATABASE_ADMIN_URL", required=False) or database_url()


def _postgres_url(name: str, *, required: bool) -> str:
    value = os.getenv(name)
    if not value and required:
        raise ConfigurationError(f"{name} is required; put it in .env or the process environment")
    if not value:
        return ""
    if not value.startswith("postgresql+psycopg://"):
        raise ConfigurationError(f"{name} must use postgresql+psycopg")
    return value


def _apply_environment(data: dict[str, object]) -> dict[str, object]:
    raw_audio = data.get("audio")
    audio = dict(raw_audio) if isinstance(raw_audio, dict) else {}
    raw_alignment = data.get("alignment")
    alignment = dict(raw_alignment) if isinstance(raw_alignment, dict) else {}
    raw_mfa = alignment.get("mfa")
    mfa = dict(raw_mfa) if isinstance(raw_mfa, dict) else {}
    for key, env_name in (
        ("ffmpeg_executable", "REGISTRY_ALIGN_FFMPEG"),
        ("ffprobe_executable", "REGISTRY_ALIGN_FFPROBE"),
    ):
        if value := os.getenv(env_name):
            audio.setdefault(key, value)
    if value := os.getenv("REGISTRY_ALIGN_MFA"):
        mfa.setdefault("executable", value)
    if audio:
        data["audio"] = audio
    if mfa:
        alignment["mfa"] = mfa
    if alignment:
        data["alignment"] = alignment
    return data


def load_config(path: Path | None) -> AppConfig:
    dotenv_path = (path.parent if path else Path.cwd()) / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)
    data: dict[str, object] = {}
    if path is not None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(f"cannot load configuration {path}: {exc}") from exc
    try:
        return AppConfig.model_validate(_apply_environment(data))
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration {path or '<defaults>'}: {exc}") from exc
