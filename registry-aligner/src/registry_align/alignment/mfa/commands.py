"""MFA version/capability-specific argument construction."""

from __future__ import annotations

from pathlib import Path

from registry_align.config import AlignmentConfig, MfaConfig
from registry_align.errors import ConfigurationError


def select_mode(config: MfaConfig, capabilities: set[str]) -> str:
    if config.model_mode != "auto":
        return config.model_mode
    if "align_hf" in capabilities and not config.dictionary:
        return "hosted"
    return "legacy"


def build_validation_command(
    executable: str,
    corpus: Path,
    validation_output: Path,
    temporary: Path,
    config: AlignmentConfig,
) -> list[str] | None:
    mode = select_mode(config.mfa, set())
    if mode == "hosted" or not config.mfa.validate_corpus:
        return None
    if not config.mfa.dictionary:
        raise ConfigurationError("legacy MFA validation requires alignment.mfa.dictionary")
    command = [
        executable,
        "validate",
        str(corpus),
        config.mfa.dictionary,
        "--output_directory",
        str(validation_output),
        "--acoustic_model_path",
        config.mfa.acoustic_model,
        "--temporary_directory",
        str(temporary),
        "--num_jobs",
        str(config.jobs),
        "--clean",
        "--overwrite",
    ]
    return command


def build_align_command(
    executable: str,
    corpus: Path,
    output: Path,
    temporary: Path,
    config: AlignmentConfig,
    capabilities: set[str],
) -> list[str]:
    mode = select_mode(config.mfa, capabilities)
    common = [
        "--output_format",
        "json",
        "--temporary_directory",
        str(temporary),
        "--num_jobs",
        str(config.jobs),
        "--clean",
        "--overwrite",
    ]
    if config.mfa.fine_tune:
        common.append("--fine_tune")
    if mode == "hosted":
        if "align_hf" not in capabilities:
            raise ConfigurationError("installed MFA does not support align_hf")
        if not config.mfa.acoustic_model:
            raise ConfigurationError("hosted MFA alignment requires alignment.mfa.acoustic_model")
        command = [
            executable,
            "align_hf",
            str(corpus),
            config.mfa.acoustic_model,
            str(output),
            *common,
        ]
        if config.mfa.dialect:
            command.extend(["--dialect", config.mfa.dialect])
        if config.mfa.use_g2p_for_oov:
            command.append("--use_g2p")
        return command
    if not config.mfa.dictionary or not config.mfa.acoustic_model:
        raise ConfigurationError(
            "legacy MFA alignment requires alignment.mfa.dictionary and acoustic_model"
        )
    legacy_command = "align_legacy" if "align_legacy" in capabilities else "align"
    command = [
        executable,
        legacy_command,
        str(corpus),
        config.mfa.dictionary,
        config.mfa.acoustic_model,
        str(output),
        *common,
    ]
    if config.mfa.use_g2p_for_oov:
        if not config.mfa.g2p_model:
            raise ConfigurationError("use_g2p_for_oov requires alignment.mfa.g2p_model")
        command.extend(["--g2p_model_path", config.mfa.g2p_model])
    return command
