"""Typer CLI; all pipeline behavior remains in AlignmentService."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from registry_align.alignment.mfa.dictionary import build_italian_dictionary
from registry_align.domain.runs import ProcessRequest
from registry_align.errors import RegistryAlignError
from registry_align.pipeline.service import AlignmentService

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
db_app = typer.Typer(no_args_is_help=True)
cache_app = typer.Typer(no_args_is_help=True)
maintenance_app = typer.Typer(no_args_is_help=True)
audio_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(cache_app, name="cache")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(audio_app, name="audio")
console = Console()
error_console = Console(stderr=True)


def _service(config: Path | None) -> AlignmentService:
    return AlignmentService.from_config(config)


def _json(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True))


def _run(action: Any, *, json_output: bool) -> Any:
    try:
        return action()
    except RegistryAlignError as exc:
        if json_output:
            _json({"status": "error", "message": str(exc), "exit_code": int(exc.exit_code)})
        else:
            error_console.print(str(exc))
        raise typer.Exit(code=int(exc.exit_code)) from exc


@app.command("doctor")
def doctor(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    registry: Annotated[Path | None, typer.Option("--registry", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).doctor(registry), json_output=json_output)
    if json_output:
        _json(result)
    else:
        for name, check in result["checks"].items():
            console.print(f"{name}: {'ok' if check.get('usable') else 'unavailable'}")
            if message := check.get("message"):
                error_console.print(message)
    if not result["usable"]:
        raise typer.Exit(code=3)


@db_app.command("check")
def db_check(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).db_check(), json_output=json_output)
    _json(result) if json_output else console.print(result)


@db_app.command("upgrade")
def db_upgrade(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).db_upgrade(), json_output=json_output)
    _json(result) if json_output else console.print(f"database revision: {result['current']}")


@db_app.command("create-dev")
def db_create_dev(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
) -> None:
    name = _run(lambda: _service(config).db_create_development(), json_output=False)
    console.print(f"development database ready: {name}")


@db_app.command("reset-test")
def db_reset_test(
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
) -> None:
    if not confirm:
        error_console.print("--confirm is required; only databases ending in `_test` are eligible")
        raise typer.Exit(code=2)
    result = _run(lambda: _service(config).db_reset_test(), json_output=False)
    console.print(f"test database reset to revision {result['current']}")


@app.command("validate")
def validate(
    registry: Annotated[Path, typer.Argument(dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).validate_registry(registry), json_output=json_output)
    payload = result.model_dump(mode="json")
    _json(payload) if json_output else console.print(
        f"valid={result.is_valid} entries={len(result.entries)} issues={len(result.issues)}"
    )
    if not result.is_valid:
        raise typer.Exit(code=2)


@app.command("build-italian-dictionary")
def build_dictionary(
    registry: Annotated[Path, typer.Argument(dir_okay=False)],
    base_dictionary: Annotated[Path, typer.Option("--base-dictionary", dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(
        lambda: build_italian_dictionary(
            registry, base_dictionary, output, _service(config).config
        ),
        json_output=json_output,
    )
    payload = {
        "output": str(result.output_path),
        "vocabulary_words": result.vocabulary_words,
        "added_words": result.added_words,
        "added_pronunciations": result.added_pronunciations,
    }
    _json(payload) if json_output else console.print(payload)


@app.command("plan")
def plan(
    registry: Annotated[Path, typer.Argument(dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    select: Annotated[list[str] | None, typer.Option("--select")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(
        lambda: _service(config).plan(registry, selected_ids=tuple(select or ())),
        json_output=json_output,
    )
    if json_output:
        _json(result.model_dump(mode="json"))
    else:
        table = Table("Recording", "Status", "Language", "Audio")
        for item in result.items:
            table.add_row(item.recording_id, item.status, item.language, item.audio_relative_path)
        console.print(table)
    if not result.is_valid:
        raise typer.Exit(code=2)


@app.command("process")
def process(
    registry: Annotated[Path, typer.Argument(dir_okay=False)],
    overwrite_existing: Annotated[bool, typer.Option("--overwrite-existing")] = False,
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    select: Annotated[list[str] | None, typer.Option("--select")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    request = ProcessRequest(
        registry_path=registry,
        overwrite_existing=overwrite_existing,
        selected_ids=tuple(select or ()),
    )
    result = _run(lambda: _service(config).process(request), json_output=json_output)
    if json_output:
        _json(result.model_dump(mode="json"))
    else:
        console.print(f"run={result.run_id} status={result.status}")
        console.print(" ".join(f"{key}={value}" for key, value in result.counts.items()))
    if result.status == "failed":
        raise typer.Exit(code=4)


@app.command("status")
def status(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).status(), json_output=json_output)
    _json(result) if json_output else console.print(result)


@app.command("history")
def history(
    recording_id: Annotated[str, typer.Argument()],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).history(recording_id), json_output=json_output)
    if json_output:
        _json(result)
    else:
        table = Table("Created", "Current", "Audio SHA-256", "Transcript SHA-256")
        for item in result:
            table.add_row(
                str(item["created_at"]),
                str(item["is_current"]),
                str(item["audio_sha256"]),
                str(item["transcript_sha256"]),
            )
        console.print(table)


@audio_app.command("fetch")
def audio_fetch(
    recording_id: Annotated[str, typer.Argument()],
    output: Annotated[Path | None, typer.Option("--output", dir_okay=False)] = None,
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(
        lambda: _service(config).fetch_audio(recording_id, output),
        json_output=json_output,
    )
    _json(result) if json_output else console.print(f"downloaded: {result['path']}")


@app.command("qc")
def qc(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).qc(), json_output=json_output)
    _json(result) if json_output else console.print(f"issues={len(result)}")


@app.command("export")
def export_command(
    formats: Annotated[list[str] | None, typer.Option("--format")] = None,
    output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None,
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    requested = tuple(formats or _service(config).config.output.formats)
    invalid = set(requested) - {"jsonl", "csv", "textgrid"}
    if invalid:
        error_console.print(f"unsupported formats: {sorted(invalid)}")
        raise typer.Exit(code=2)
    result = _run(lambda: _service(config).export(requested, output), json_output=json_output)
    _json([str(path) for path in result]) if json_output else console.print(
        f"exported {len(result)} file(s)"
    )


@cache_app.command("status")
def cache_status(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).cache_status(), json_output=json_output)
    _json(result) if json_output else console.print(result)


@maintenance_app.command("prune-runs")
def prune_runs(
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    result = _run(lambda: _service(config).prune_runs(dry_run=dry_run), json_output=json_output)
    payload = {"dry_run": dry_run, "run_ids": [str(item) for item in result], "count": len(result)}
    _json(payload) if json_output else console.print(payload)


if __name__ == "__main__":
    app()
