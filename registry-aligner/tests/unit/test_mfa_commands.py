from pathlib import Path

import pytest

from registry_align.alignment.mfa.commands import (
    build_align_command,
    build_validation_command,
)
from registry_align.config import AlignmentConfig, MfaConfig
from registry_align.errors import ConfigurationError


def test_builds_legacy_argument_array() -> None:
    config = AlignmentConfig(
        jobs=3,
        mfa=MfaConfig(
            acoustic_model="italian_mfa",
            dictionary="italian_mfa",
            g2p_model="italian_mfa",
            use_g2p_for_oov=True,
        ),
    )

    command = build_align_command(
        "mfa", Path("corpus"), Path("output"), Path("temp"), config, {"align"}
    )

    assert command[:6] == [
        "mfa",
        "align",
        "corpus",
        "italian_mfa",
        "italian_mfa",
        "output",
    ]
    assert "--output_format" in command
    assert command[-2:] == ["--g2p_model_path", "italian_mfa"]


def test_builds_hosted_argument_array_without_dictionary(tmp_path: Path) -> None:
    model = tmp_path / "italian-model"
    model.mkdir()
    config = AlignmentConfig(
        mfa=MfaConfig(model_mode="hosted", acoustic_model=str(model), dictionary="")
    )

    command = build_align_command(
        "mfa", Path("corpus"), Path("output"), Path("temp"), config, {"align_hf"}
    )

    assert command[:5] == ["mfa", "align_hf", "corpus", str(model), "output"]


def test_validation_command_is_explicit_and_legacy_only() -> None:
    legacy = AlignmentConfig(mfa=MfaConfig(acoustic_model="italian", dictionary="italian"))
    hosted = AlignmentConfig(
        mfa=MfaConfig(model_mode="hosted", acoustic_model="local", dictionary="")
    )

    command = build_validation_command(
        "mfa", Path("corpus"), Path("validation"), Path("temp"), legacy
    )

    assert command is not None
    assert command[:4] == ["mfa", "validate", "corpus", "italian"]
    assert (
        build_validation_command("mfa", Path("corpus"), Path("validation"), Path("temp"), hosted)
        is None
    )


def test_hosted_mode_requires_supported_capability() -> None:
    config = AlignmentConfig(
        mfa=MfaConfig(model_mode="hosted", acoustic_model="italian", dictionary="")
    )

    with pytest.raises(ConfigurationError, match="does not support align_hf"):
        build_align_command("mfa", Path("corpus"), Path("output"), Path("temp"), config, set())


def test_prefers_explicit_legacy_command_when_available() -> None:
    config = AlignmentConfig(mfa=MfaConfig(acoustic_model="italian", dictionary="italian"))

    command = build_align_command(
        "mfa",
        Path("corpus"),
        Path("output"),
        Path("temp"),
        config,
        {"align", "align_legacy", "align_hf"},
    )

    assert command[1] == "align_legacy"
