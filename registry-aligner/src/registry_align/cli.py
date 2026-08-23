"""Thin Typer shell over :class:`AlignmentService`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from registry_align.alignment.mfa.dictionary import build_italian_dictionary
from registry_align.config import load_config
from registry_align.domain.runs import ProcessRequest, ProcessResult
from registry_align.errors import ConfigurationError, ExitCode, RegistryAlignError
from registry_align.events import Event, EventObserver, Issue
from registry_align.pipeline.service import AlignmentService

app = typer.Typer(no_args_is_help=True, help="Prepare and align JSON recording registries.")
console = Console()
error_console = Console(stderr=True)


def _dump_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _issue_payload(issue: Issue) -> dict[str, Any]:
    return issue.model_dump(mode="json", exclude_none=True)


def _print_issue(issue: Issue) -> None:
    location = (
        f" entry {issue.details['source_entry_index']}"
        if "source_entry_index" in issue.details
        else ""
    )
    style = "red" if issue.severity == "error" else "yellow"
    error_console.print(
        f"{issue.severity.value.upper()} {issue.code}{location}: {issue.message}", style=style
    )


def _fail(error: RegistryAlignError, *, json_output: bool) -> None:
    if json_output:
        _dump_json(
            {
                "schema_version": "1.0",
                "status": "error",
                "error": {"type": type(error).__name__, "message": str(error)},
                "exit_code": int(error.exit_code),
            }
        )
    else:
        error_console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(code=int(error.exit_code))


class CliObserver(EventObserver):
    def __init__(self, *, json_output: bool, quiet: bool = False) -> None:
        self.json_output = json_output
        self.quiet = quiet

    def on_event(self, event: Event) -> None:
        if self.quiet:
            return
        if self.json_output:
            _dump_json(
                {
                    "schema_version": "1.0",
                    "type": "event",
                    **event.model_dump(mode="json", exclude_none=True),
                }
            )
        elif event.recording_id:
            console.print(f"{event.name}: {event.recording_id} {event.status or ''}".rstrip())
        else:
            console.print(f"{event.name}: {event.status or ''}".rstrip())


def _default_output(registry: Path) -> Path:
    return registry.parent / f"{registry.stem}.alignment"


def _configure_service(
    config: Path | None,
    *,
    jobs: int | None,
    continue_on_error: bool,
    observer: EventObserver | None = None,
) -> AlignmentService:
    service = AlignmentService.from_config(config, observer=observer)
    alignment = service.config.alignment.model_copy(
        update={
            "jobs": jobs if jobs is not None else service.config.alignment.jobs,
            "continue_on_error": continue_on_error,
        }
    )
    service.config = service.config.model_copy(update={"alignment": alignment})
    return service


def _process_payload(command: str, result: ProcessResult) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "type": "result",
        "command": command,
        **result.model_dump(mode="json", exclude_none=True),
    }


def _finish_process(command: str, result: ProcessResult, *, json_output: bool) -> None:
    if json_output:
        _dump_json(_process_payload(command, result))
    else:
        console.print(f"{command} {result.status}: {result.run_id}")
        console.print(" ".join(f"{key}={value}" for key, value in result.counts.items()))
        for issue in result.issues:
            _print_issue(issue)
    if result.status == "partial":
        raise typer.Exit(code=int(ExitCode.PARTIAL_SUCCESS))
    if result.status == "failed":
        raise typer.Exit(code=int(ExitCode.PROCESSING_FAILURE))


@app.command("init")
def initialize(
    directory: Annotated[Path, typer.Argument(help="Directory receiving template files.")] = Path(
        "."
    ),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite existing template files.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit one structured JSON result.")
    ] = False,
) -> None:
    try:
        config_path, schema_path = AlignmentService.initialize(directory, force=force)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    if json_output:
        _dump_json(
            {
                "schema_version": "1.0",
                "command": "init",
                "status": "ok",
                "files": [str(config_path), str(schema_path)],
            }
        )
    else:
        console.print(f"Created {config_path}")
        console.print(f"Created {schema_path}")


@app.command("validate")
def validate(
    registry: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    fail_fast: Annotated[
        bool, typer.Option("--fail-fast", help="Stop after the first bad entry.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit one structured JSON result.")
    ] = False,
) -> None:
    try:
        result = AlignmentService.from_config(config).validate_registry(
            registry, fail_fast=fail_fast
        )
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    payload = {
        "schema_version": "1.0",
        "command": "validate",
        "status": "ok" if result.is_valid else "invalid",
        "counts": {
            "valid_entries": len(result.entries),
            "issues": len(result.issues),
            "errors": result.error_count,
        },
        "issues": [_issue_payload(issue) for issue in result.issues],
        "entries": [
            {
                "id": entry.id,
                "source_entry_index": entry.source_entry_index,
                "audio_relative_path": entry.audio_relative_path,
                "language": entry.language,
                "id_was_derived": entry.id_was_derived,
            }
            for entry in result.entries
        ],
    }
    if json_output:
        _dump_json(payload)
    else:
        console.print(f"Validated {len(result.entries)} entries; {result.error_count} error(s).")
        for issue in result.issues:
            _print_issue(issue)
    if not result.is_valid:
        raise typer.Exit(code=int(ExitCode.INPUT_FAILURE))


@app.command("plan")
def plan(
    registry: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    select: Annotated[
        list[str] | None, typer.Option("--select", help="Select a recording ID; repeatable.")
    ] = None,
    fail_fast: Annotated[bool, typer.Option("--fail-fast")] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit one structured JSON result.")
    ] = False,
) -> None:
    try:
        result = AlignmentService.from_config(config).plan(
            registry, selected_ids=tuple(select or ()), fail_fast=fail_fast
        )
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    payload = {
        "schema_version": "1.0",
        "command": "plan",
        "status": "ok" if result.is_valid else "invalid",
        "counts": result.counts,
        "issues": [_issue_payload(issue) for issue in result.issues],
        "entries": [item.model_dump(mode="json") for item in result.items],
    }
    if json_output:
        _dump_json(payload)
    else:
        table = Table(title="Registry plan")
        table.add_column("Recording")
        table.add_column("Status")
        table.add_column("Language")
        table.add_column("Audio")
        for item in result.items:
            table.add_row(item.recording_id, item.status, item.language, item.audio_relative_path)
        console.print(table)
        console.print(" ".join(f"{key}={value}" for key, value in result.counts.items()))
        for issue in result.issues:
            _print_issue(issue)
    if not result.is_valid:
        raise typer.Exit(code=int(ExitCode.INPUT_FAILURE))


@app.command("doctor")
def doctor(
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = AlignmentService.from_config(config).doctor(output)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    if json_output:
        _dump_json({"command": "doctor", **result})
    else:
        for name, check in result["checks"].items():
            console.print(f"{name}: {'ok' if check.get('usable') else 'unavailable'}")
            if check.get("message"):
                error_console.print(str(check["message"]))
            for command in check.get("remediation_commands", ()):
                error_console.print(f"Run: {command}")
        console.print(f"usable={result['usable']}")
    if not result["usable"]:
        raise typer.Exit(code=int(ExitCode.DEPENDENCY_FAILURE))


@app.command("build-italian-dictionary")
def build_italian_dictionary_command(
    registry: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    base_dictionary: Annotated[
        Path, typer.Option("--base-dictionary", exists=False, dir_okay=False)
    ],
    output: Annotated[Path, typer.Option("--output", dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = build_italian_dictionary(
            registry,
            base_dictionary,
            output,
            load_config(config),
        )
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    counts = {
        "vocabulary_words": result.vocabulary_words,
        "added_words": result.added_words,
        "added_pronunciations": result.added_pronunciations,
    }
    payload = {
        "schema_version": "1.0",
        "command": "build-italian-dictionary",
        "status": "ok",
        "output": str(result.output_path),
        "counts": counts,
    }
    if json_output:
        _dump_json(payload)
    else:
        console.print(f"Built {result.output_path}")
        console.print(" ".join(f"{key}={value}" for key, value in counts.items()))


def _run_pipeline(
    command: str,
    registry: Path,
    config: Path | None,
    output: Path | None,
    jobs: int | None,
    resume: bool,
    force: bool,
    continue_on_error: bool,
    select: list[str] | None,
    json_output: bool,
    quiet: bool,
) -> None:
    observer = CliObserver(json_output=json_output, quiet=quiet)
    try:
        service = _configure_service(
            config,
            jobs=jobs,
            continue_on_error=continue_on_error,
            observer=observer,
        )
        request = ProcessRequest(
            registry_path=registry,
            output_directory=output or _default_output(registry),
            resume=resume,
            force=force,
            selected_ids=tuple(select or ()),
        )
        result = service.prepare(request) if command == "prepare" else service.process(request)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    _finish_process(command, result, json_output=json_output)


@app.command("prepare")
def prepare(
    registry: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None,
    jobs: Annotated[int | None, typer.Option("--jobs", min=1)] = None,
    resume: Annotated[bool, typer.Option("--resume/--no-resume")] = True,
    force: Annotated[bool, typer.Option("--force")] = False,
    continue_on_error: Annotated[bool, typer.Option("--continue-on-error/--fail-fast")] = True,
    select: Annotated[list[str] | None, typer.Option("--select")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    _run_pipeline(
        "prepare",
        registry,
        config,
        output,
        jobs,
        resume,
        force,
        continue_on_error,
        select,
        json_output,
        quiet,
    )


@app.command("process")
def process(
    registry: Annotated[Path, typer.Argument(exists=False, dir_okay=False)],
    config: Annotated[Path | None, typer.Option("--config", dir_okay=False)] = None,
    output: Annotated[Path | None, typer.Option("--output", file_okay=False)] = None,
    jobs: Annotated[int | None, typer.Option("--jobs", min=1)] = None,
    resume: Annotated[bool, typer.Option("--resume/--no-resume")] = True,
    force: Annotated[bool, typer.Option("--force")] = False,
    continue_on_error: Annotated[bool, typer.Option("--continue-on-error/--fail-fast")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    select: Annotated[list[str] | None, typer.Option("--select")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    if dry_run:
        plan(registry, config, select, not continue_on_error, json_output)
        return
    _run_pipeline(
        "process",
        registry,
        config,
        output,
        jobs,
        resume,
        force,
        continue_on_error,
        select,
        json_output,
        quiet,
    )


@app.command("status")
def status(
    output: Annotated[Path, typer.Argument(file_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = AlignmentService.status(output)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    if json_output:
        _dump_json({"command": "status", **result})
    else:
        console.print(f"status={result['status']}")
        console.print(" ".join(f"{key}={value}" for key, value in result["counts"].items()))


@app.command("qc")
def qc(
    output: Annotated[Path, typer.Argument(file_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        result = AlignmentService.qc(output)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    if json_output:
        _dump_json({"command": "qc", **result})
    else:
        console.print(f"issues={result['issues']}")


@app.command("export")
def export_command(
    output: Annotated[Path, typer.Argument(file_okay=False)],
    formats: Annotated[list[str] | None, typer.Option("--format")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    requested = tuple(formats or ("jsonl",))
    invalid = set(requested) - {"jsonl", "csv", "textgrid"}
    if invalid:
        _fail(
            ConfigurationError(f"unsupported export formats: {sorted(invalid)}"),
            json_output=json_output,
        )
    try:
        result = AlignmentService.export(output, requested)
    except RegistryAlignError as exc:
        _fail(exc, json_output=json_output)
    if json_output:
        _dump_json({"command": "export", **result})
    else:
        console.print(f"Exported {len(result['files'])} file(s).")


if __name__ == "__main__":
    app()
