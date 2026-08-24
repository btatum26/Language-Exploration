from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from typer.testing import CliRunner

from registry_align.cli import app


class RegistryFactory(Protocol):
    def __call__(self, document: object, audio_paths: tuple[str, ...] = ...) -> Path: ...


runner = CliRunner()


def test_help_exposes_postgresql_workflow() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "process" in result.stdout
    assert "history" in result.stdout
    assert "audio" in result.stdout
    assert "maintenance" in result.stdout


def test_audio_fetch_exposes_output_path() -> None:
    result = runner.invoke(app, ["audio", "fetch", "--help"])

    assert result.exit_code == 0
    assert "--output" in result.stdout


def test_validate_json_output(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["validate", str(registry_factory(canonical_document)), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["entries"][0]["id"] == "lesson-1-luca"


def test_process_exposes_explicit_overwrite_option() -> None:
    result = runner.invoke(app, ["process", "--help"])

    assert result.exit_code == 0
    assert "--overwrite-existing" in result.stdout


def test_reset_test_requires_confirmation() -> None:
    result = runner.invoke(app, ["db", "reset-test"])

    assert result.exit_code == 2
    assert "--confirm is required" in result.output
