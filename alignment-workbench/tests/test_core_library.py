from pathlib import Path
from uuid import uuid4

import pytest

from application import FileRecoveryOutbox, LocalAudioStorage, create_workbench
from application.core_library import core_publication
from application.errors import PersistenceIntegrityError, WorkbenchStartupError
from application.validation import library_content_sha256
from models import Library
from tests.test_application_api import MemoryPersistenceStore


def make_api(store, tmp_path):
    return create_workbench(
        persistence=store,
        audio_storage=LocalAudioStorage(tmp_path / "audio"),
        recovery_outbox=FileRecoveryOutbox(tmp_path / "recovery"),
    )


def test_core_initialization_is_exact_and_idempotent(tmp_path: Path) -> None:
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    version = api.ensure_core_library()
    assert api.ensure_core_library() == version
    assert make_api(store, tmp_path).ensure_core_library() == version
    assert len(store.libraries) == len(store.versions) == 1
    assert api.libraries.get(version.library_id).name == "Core annotations"
    assert version.version_label == "0.1"
    assert version.content_sha256 == library_content_sha256(version)
    assert [e.entry_key for e in version.entries] == [
        "silence",
        "unknown",
        "word",
        "syllable",
        "noise",
        "breath",
        "marker",
    ]
    assert all(e.allowed_geometry_types == ("time_interval",) for e in version.entries[:-1])
    assert version.entries[-1].allowed_geometry_types == ("point",)


def test_missing_publication_does_not_rewrite_a_later_version(tmp_path: Path) -> None:
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    later = core_publication(library.id).model_copy(update={"version_label": "0.2", "entries": ()})
    later = later.model_copy(update={"content_sha256": library_content_sha256(later)})
    api.libraries.publish_version(later)
    version = api.ensure_core_library()
    assert version.library_id == library.id
    assert version.version_label == "0.1"
    assert version.id != later.id
    assert api.libraries.get_version(later.id) == later
    assert len(store.libraries) == 1
    assert len(store.versions) == 2


def test_conflicting_publication_is_preserved(tmp_path: Path) -> None:
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    conflicting = core_publication(library.id).model_copy(update={"entries": ()})
    conflicting = conflicting.model_copy(
        update={"content_sha256": library_content_sha256(conflicting)}
    )
    api.libraries.publish_version(conflicting)
    with pytest.raises(WorkbenchStartupError, match="core@0.1 conflicts"):
        api.ensure_core_library()
    assert tuple(store.versions.values()) == (conflicting,)


@pytest.mark.parametrize("stage", ["create_library", "publish_library_version"])
def test_competing_winner_is_reread_and_verified(tmp_path, monkeypatch, stage):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    if stage == "publish_library_version":
        api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    original = getattr(store, stage)

    def race(value):
        original(value)
        raise PersistenceIntegrityError("competing initializer won")

    monkeypatch.setattr(store, stage, race)
    version = api.ensure_core_library()
    assert version == next(iter(store.versions.values()))
    assert len(store.libraries) == len(store.versions) == 1


def test_unrelated_publication_failure_is_not_swallowed(tmp_path, monkeypatch):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)

    def rejected(_value):
        raise PersistenceIntegrityError("write rejected")

    monkeypatch.setattr(store, "publish_library_version", rejected)
    with pytest.raises(PersistenceIntegrityError, match="write rejected"):
        api.ensure_core_library()


def test_identical_content_under_other_label_is_not_renamed(tmp_path):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    other = core_publication(library.id).model_copy(update={"version_label": "0.2"})
    api.libraries.publish_version(other)
    with pytest.raises(WorkbenchStartupError, match="already exists as core@0.2"):
        api.ensure_core_library()
    assert api.libraries.list_versions(library.id) == (other,)
