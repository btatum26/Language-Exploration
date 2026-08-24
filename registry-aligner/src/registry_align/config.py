"""Versioned, strict TOML configuration."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

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

    @field_validator("language")
    @classmethod
    def language_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("language cannot be blank")
        return value


class AudioConfig(StrictModel):
    editor_pcm_mode: Literal["native"] = "native"
    alignment_sample_rate_hz: int = Field(default=16000, gt=0)
    alignment_channels: int = Field(default=1, gt=0)
    ffmpeg_executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"


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
    mfa: MfaConfig = Field(default_factory=MfaConfig)


class OutputConfig(StrictModel):
    write_sqlite: bool = True
    write_jsonl: bool = True
    write_textgrids: bool = False
    retain_backend_output: bool = True


class AppConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    input: InputConfig = Field(default_factory=InputConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def _apply_environment(data: dict[str, object]) -> dict[str, object]:
    raw_audio = data.get("audio")
    audio: dict[str, object] = dict(raw_audio) if isinstance(raw_audio, dict) else {}
    raw_alignment = data.get("alignment")
    alignment: dict[str, object] = dict(raw_alignment) if isinstance(raw_alignment, dict) else {}
    raw_mfa = alignment.get("mfa")
    mfa: dict[str, object] = dict(raw_mfa) if isinstance(raw_mfa, dict) else {}
    environment_values = {
        "ffmpeg_executable": os.getenv("REGISTRY_ALIGN_FFMPEG"),
        "ffprobe_executable": os.getenv("REGISTRY_ALIGN_FFPROBE"),
    }
    for key, value in environment_values.items():
        if value and key not in audio:
            audio[key] = value
    mfa_executable = os.getenv("REGISTRY_ALIGN_MFA")
    if mfa_executable and "executable" not in mfa:
        mfa["executable"] = mfa_executable
    if audio:
        data["audio"] = audio
    if mfa:
        alignment["mfa"] = mfa
    if alignment:
        data["alignment"] = alignment
    return data


def load_config(path: Path | None) -> AppConfig:
    if path is None:
        return AppConfig.model_validate(_apply_environment({}))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"cannot read configuration {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"invalid TOML configuration {path}: {exc}") from exc
    try:
        return AppConfig.model_validate(_apply_environment(data))
    except ValidationError as exc:
        raise ConfigurationError(f"invalid configuration {path}: {exc}") from exc
