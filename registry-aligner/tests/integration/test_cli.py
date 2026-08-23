from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from typer.testing import CliRunner

from registry_align.cli import app


class RegistryFactory(Protocol):
    def __call__(self, document: object, audio_paths: tuple[str, ...] = ...) -> Path: ...


runner = CliRunner()


def test_init_creates_templates_and_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "templates"

    first = runner.invoke(app, ["init", str(target), "--json"])
    second = runner.invoke(app, ["init", str(target), "--json"])

    assert first.exit_code == 0
    assert (target / "registry-align.toml").exists()
    schema = json.loads((target / "canonical-registry.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert second.exit_code == 2
    assert json.loads(second.stdout)["error"]["type"] == "InitializationError"


def test_validate_json_output(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    registry = registry_factory(canonical_document)

    result = runner.invoke(app, ["validate", str(registry), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "validate"
    assert payload["status"] == "ok"
    assert payload["counts"]["valid_entries"] == 1
    assert payload["entries"][0]["id"] == "lesson-1-luca"


def test_validate_returns_input_exit_code_for_invalid_registry(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text('{"schema_version":"2.0","entries":[]}', encoding="utf-8")

    result = runner.invoke(app, ["validate", str(registry), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["issues"][0]["code"] == "SCHEMA_VERSION_UNSUPPORTED"


def test_validate_human_output_handles_issue_severity(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text('{"schema_version":"2.0","entries":[]}', encoding="utf-8")

    result = runner.invoke(app, ["validate", str(registry)])

    assert result.exit_code == 2
    assert "SCHEMA_VERSION_UNSUPPORTED" in result.output


def test_plan_json_selection(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    registry = registry_factory(canonical_document)

    result = runner.invoke(app, ["plan", str(registry), "--select", "lesson-1-luca", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["counts"]["selected"] == 1
    assert payload["entries"][0]["status"] == "new"


def test_validate_reports_registry_read_errors_as_json(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    result = runner.invoke(app, ["validate", str(missing), "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error"]["type"] == "RegistryReadError"
