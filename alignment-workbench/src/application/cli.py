"""Executable smoke entry point for the configured workbench application."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from application.contracts import CreateRecordingCommand
from application.errors import WorkbenchError, WorkbenchStartupError
from application.runtime import WorkbenchApplication, WorkbenchSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch and validate Alignment Workbench")
    parser.add_argument("--import-audio", type=Path, help="optional WAV or MP3 file to import")
    parser.add_argument("--name", help="display name for an imported recording")
    parser.add_argument("--language", default="und", help="language tag for an import")
    parser.add_argument("--limit", type=int, default=100, help="maximum recordings to list")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        settings = WorkbenchSettings.from_environment()
        application = WorkbenchApplication(settings)
        with application:
            print("application=started")
            _print_discovery(application, limit=arguments.limit)
            if arguments.import_audio is not None:
                _exercise_import(
                    application,
                    arguments.import_audio,
                    name=arguments.name,
                    language=arguments.language,
                )
        print("application=shutdown")
    except (WorkbenchError, ValueError) as exc:
        print(f"alignment-workbench: {exc}", file=sys.stderr)
        return 2
    return 0


def _print_discovery(application: WorkbenchApplication, *, limit: int) -> None:
    recordings = application.list_recordings(limit=limit)
    libraries = application.list_annotation_libraries()
    print(f"recordings={len(recordings)}")
    for recording in recordings:
        print(
            "recording "
            f"id={recording.recording_id} name={recording.display_name!r} "
            f"revision={recording.revision_number} audio={recording.audio_status.value}"
        )
    print(f"libraries={len(libraries)}")
    for library in libraries:
        version = library.latest_version_label or "none"
        print(
            "library "
            f"id={library.library_id} name={library.name!r} "
            f"latest_version={version} entries={library.entry_count}"
        )


def _exercise_import(
    application: WorkbenchApplication,
    source: Path,
    *,
    name: str | None,
    language: str,
) -> None:
    recording_id = uuid4()
    imported = application.import_recording(
        CreateRecordingCommand(
            recording_id=recording_id,
            source_audio_path=source,
            name=name or source.stem,
            language=language,
        )
    )
    imported.close()
    listed = application.list_recordings()
    if not any(item.recording_id == recording_id for item in listed):
        raise WorkbenchStartupError("imported recording did not appear in discovery")
    reopened = application.open_recording(recording_id)
    reopened.close()
    print(f"imported_recording={recording_id}")
    print("import_open_close=ok")


if __name__ == "__main__":
    raise SystemExit(main())
