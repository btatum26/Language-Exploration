from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from registry_align.cli import app
from registry_align.domain.runs import ProcessResult
from registry_align.events import Event

runner = CliRunner()


def test_process_json_emits_events_and_final_result(monkeypatch: Any, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")

    class StubService:
        def __init__(self, observer: Any) -> None:
            self.observer = observer

        def process(self, request: Any) -> ProcessResult:
            self.observer.on_event(Event(name="run_started", status="running"))
            return ProcessResult(
                run_id="run_test",
                status="complete",
                counts={"completed": 1},
                output_locations={"database": request.output_directory / "corpus.sqlite3"},
            )

    def configure(*args: Any, **kwargs: Any) -> StubService:
        del args
        return StubService(kwargs["observer"])

    monkeypatch.setattr("registry_align.cli._configure_service", configure)

    result = runner.invoke(app, ["process", str(registry), "--json"])

    lines = [__import__("json").loads(line) for line in result.stdout.splitlines()]
    assert result.exit_code == 0
    assert lines[0]["type"] == "event"
    assert lines[-1]["type"] == "result"
    assert lines[-1]["status"] == "complete"


def test_process_partial_returns_exit_code_four(monkeypatch: Any, tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")

    class StubService:
        def process(self, request: Any) -> ProcessResult:
            del request
            return ProcessResult(
                run_id="run_partial",
                status="partial",
                counts={"completed": 1, "failed": 1},
            )

    monkeypatch.setattr(
        "registry_align.cli._configure_service", lambda *args, **kwargs: StubService()
    )

    result = runner.invoke(app, ["process", str(registry), "--json", "--quiet"])

    assert result.exit_code == 4
