from pathlib import Path
from typing import Any, Protocol

from registry_align.pipeline.service import AlignmentService


class RegistryFactory(Protocol):
    def __call__(self, document: object, audio_paths: tuple[str, ...] = ...) -> Path: ...


def test_plan_selects_and_ignores_without_creating_output(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    canonical_document["entries"].append(
        {
            "id": "lesson-2-fil",
            "audio_path": "audio/two.wav",
            "transcript": "ciao",
        }
    )
    registry_path = registry_factory(canonical_document, ("audio/one.wav", "audio/two.wav"))
    before = set(registry_path.parent.rglob("*"))

    result = AlignmentService.from_config().plan(registry_path, selected_ids=("lesson-2-fil",))

    assert result.is_valid
    assert [item.status for item in result.items] == ["ignored", "new"]
    assert result.counts["selected"] == 1
    assert result.counts["ignored"] == 1
    assert set(registry_path.parent.rglob("*")) == before


def test_unknown_selected_id_is_an_error(
    registry_factory: RegistryFactory, canonical_document: dict[str, Any]
) -> None:
    result = AlignmentService.from_config().plan(
        registry_factory(canonical_document), selected_ids=("missing",)
    )

    assert not result.is_valid
    assert result.issues[-1].code == "SELECTED_ID_UNKNOWN"


def test_blocked_count_counts_entries_not_issues(registry_factory: RegistryFactory) -> None:
    document = {
        "schema_version": "1.0",
        "entries": [
            {"id": "bad", "audio_path": "missing.wav", "transcript": ""},
        ],
    }

    result = AlignmentService.from_config().plan(registry_factory(document))

    assert result.counts["blocked"] == 1
    assert result.counts["issues"] == 3
