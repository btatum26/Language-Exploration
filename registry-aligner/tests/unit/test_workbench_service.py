from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from registry_align.config import AppConfig, SourceConfig
from registry_align.errors import ConfigurationError
from registry_align.pipeline.service import AlignmentService
from registry_align.workbench import RegistryWorkbenchService


class FakeRepository:
    def __init__(self) -> None:
        self.saved = []
        self.commits = 0
        self.segment_rows = [
            {
                "id": UUID("00000000-0000-0000-0000-000000000001"),
                "kind": "phone",
                "start_sample": 0,
                "end_sample": 100,
                "parent_segment_id": UUID("00000000-0000-0000-0000-000000000010"),
            },
            {
                "id": UUID("00000000-0000-0000-0000-000000000002"),
                "kind": "phone",
                "start_sample": 100,
                "end_sample": 180,
                "parent_segment_id": UUID("00000000-0000-0000-0000-000000000010"),
            },
        ]

    def catalog_recordings(self, **_query):
        return {
            "items": [
                {
                    "recording_id": "one",
                    "recording_version_id": uuid4(),
                    "transcript_raw": "ciao",
                    "language": "it",
                    "audio_relative_path": "missing.wav",
                }
            ],
            "total": 1,
            "has_more": False,
        }

    def segments_by_ids(self, segment_ids):
        requested = set(segment_ids)
        rows = [item for item in self.segment_rows if str(item["id"]) in requested]
        parent_id = "00000000-0000-0000-0000-000000000010"
        if parent_id in requested:
            rows.append(
                {
                    "id": UUID(parent_id),
                    "kind": "word",
                    "start_sample": 0,
                    "end_sample": 180,
                    "parent_segment_id": None,
                }
            )
        return rows

    def save_revision(self, revision):
        self.saved.append(revision)
        return UUID(revision.revision_id)

    def speakers(self, text=""):
        return [{"id": "speaker-1", "display_name": "Speaker One", "language": "it"}]

    def create_speaker(self, speaker_id, display_name, language):
        return {"id": speaker_id, "display_name": display_name, "language": language}


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def commit(self):
        self.repository.commits += 1

    def rollback(self):
        return None


class FakeFactory:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository

    def __call__(self):
        return FakeUnitOfWork(self.repository)


@pytest.fixture
def service() -> tuple[RegistryWorkbenchService, FakeRepository]:
    repository = FakeRepository()
    alignment = AlignmentService(AppConfig(), uow_factory=FakeFactory(repository))
    return RegistryWorkbenchService(alignment), repository


def test_catalog_queries_metadata_and_reports_missing_local_audio(service) -> None:
    workbench, _repository = service
    page = workbench.catalog(text="ciao", offset=0, limit=50)
    assert page["total"] == 1
    assert page["items"][0]["audio_available"] is False


def test_update_revision_is_immutable_and_committed(service) -> None:
    workbench, repository = service
    segment_id = str(repository.segment_rows[0]["id"])
    result = workbench.save_revision(
        segment_id=segment_id,
        base_run_id="run",
        start_sample=2,
        end_sample=98,
        label="a",
        review_state="accepted",
    )
    assert result["review_state"] == "accepted"
    assert repository.saved[0].segment_id == segment_id
    assert repository.saved[0].start_sample_override == 2
    assert repository.commits == 1


def test_speaker_catalog_and_creation_use_repository_transaction(service) -> None:
    workbench, repository = service
    assert workbench.speakers()[0]["id"] == "speaker-1"
    created = workbench.create_speaker("speaker-2", "Speaker Two", "it")
    assert created["display_name"] == "Speaker Two"
    assert repository.commits == 1


def test_split_and_merge_validate_adjacency_and_hierarchy(service) -> None:
    workbench, repository = service
    first, second = (str(item["id"]) for item in repository.segment_rows)
    parent = str(repository.segment_rows[0]["parent_segment_id"])
    split = workbench.save_revision(
        segment_id=first,
        base_run_id="run",
        start_sample=None,
        end_sample=None,
        label=None,
        review_state="accepted",
        operation="split",
        affected_segment_ids=(first,),
        replacement_segments=(
            {
                "segment_id": f"{first}:left",
                "label": "a",
                "start_sample": 0,
                "end_sample": 40,
                "parent_segment_id": parent,
            },
            {
                "segment_id": f"{first}:right",
                "label": "b",
                "start_sample": 40,
                "end_sample": 100,
                "parent_segment_id": parent,
            },
        ),
    )
    assert split["review_state"] == "accepted"
    assert repository.saved[-1].operation == "split"

    workbench.save_revision(
        segment_id=first,
        base_run_id="run",
        start_sample=None,
        end_sample=None,
        label=None,
        review_state="accepted",
        operation="merge",
        affected_segment_ids=(first, second),
        replacement_segments=(
            {
                "segment_id": f"{first}:merged",
                "label": "ab",
                "start_sample": 0,
                "end_sample": 180,
                "parent_segment_id": parent,
            },
        ),
    )
    assert repository.saved[-1].operation == "merge"

    with pytest.raises(ConfigurationError, match="adjacent"):
        repository.segment_rows[1]["start_sample"] = 101
        workbench.save_revision(
            segment_id=first,
            base_run_id="run",
            start_sample=None,
            end_sample=None,
            label=None,
            review_state="accepted",
            operation="merge",
            affected_segment_ids=(first, second),
            replacement_segments=(
                {
                    "segment_id": "bad",
                    "label": "bad",
                    "start_sample": 0,
                    "end_sample": 180,
                    "parent_segment_id": parent,
                },
            ),
        )


def test_new_audio_uses_one_content_hash_local_artifact(tmp_path) -> None:
    repository = FakeRepository()
    config = AppConfig(source=SourceConfig(root=tmp_path / "corpus"))
    alignment = AlignmentService(config, uow_factory=FakeFactory(repository))
    workbench = RegistryWorkbenchService(alignment)
    source = tmp_path / "take.wav"
    source.write_bytes(b"synthetic-audio-bytes")

    first, logical = workbench._canonicalize_local_audio(source)
    second, repeated_logical = workbench._canonicalize_local_audio(source)

    assert first == second
    assert logical == repeated_logical
    assert logical.startswith("workbench-recordings/")
    assert first.read_bytes() == source.read_bytes()
