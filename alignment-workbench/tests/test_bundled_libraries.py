import json
import shutil
from uuid import uuid4

import pytest

from application import CreateRecordingCommand
from application.bundled_libraries import BUNDLE_ROOT, load_bundle
from application.errors import (
    GeometryNotAllowedError,
    GeometryOutOfBoundsError,
    PersistenceIntegrityError,
    WorkbenchStartupError,
)
from application.validation import library_content_sha256
from models import ConceptRef, LibraryEntry, PointGeometry, TimeIntervalGeometry
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_gui import _write_wav


def exercise_bundles(api, tmp_path):
    versions = api.ensure_bundled_libraries()
    assert api.ensure_bundled_libraries() == versions
    assert {item.namespace: item.entry_count for item in api.list_annotation_libraries()} == {
        "core": 7,
        "phonetics": 29,
        "prosody": 3,
    }
    core, phonetics, prosody = versions
    assert {e.entry_key for e in prosody.entries} == {"pitch-rise", "pitch-fall", "pitch-steady"}
    assert {e.metadata["ipa_symbol"] for e in phonetics.entries} == set(
        "a e ɛ i o ɔ u p b t d k ɡ f v s z ʃ ʒ m n ɲ ŋ l ʎ r ɾ j w".split()
    )
    for version in versions:
        restored = api.libraries.get_version(version.id)
        assert restored == version
        assert restored.content_sha256 == library_content_sha256(restored)
    for entry in phonetics.entries:
        assert entry.metadata["category_descriptor"]["name"] in {"Vowels", "Consonants"}
        assert entry.metadata["features"] and entry.metadata["aliases"]
        assert entry.allowed_geometry_types == ("time_interval",)
    source = tmp_path / "bundles.wav"
    _write_wav(source)
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name="Bundles",
            language="und",
        )
    )
    assert session.pinned_libraries == ()
    for version in versions:
        session.pin_library_version(version)
    assert session.undo() and session.pinned_libraries == (core, phonetics)
    assert session.redo() and session.pinned_libraries == versions
    for reference in ("core@0.1:word", "phonetics@0.1:epsilon", "prosody@0.1:pitch-rise"):
        session.create_annotation(
            concept_ref=ConceptRef(reference),
            geometry=TimeIntervalGeometry(start_sample=10, end_sample=100),
        )
    for sample in (0, session.audio.asset.frame_count - 1):
        session.create_annotation(
            concept_ref=ConceptRef("core@0.1:marker"),
            geometry=PointGeometry(start_sample=sample),
            label="Edge",
        )
    with pytest.raises(GeometryOutOfBoundsError):
        session.create_annotation(
            concept_ref=ConceptRef("core@0.1:marker"),
            geometry=PointGeometry(start_sample=session.audio.asset.frame_count),
        )
    with pytest.raises(GeometryNotAllowedError):
        session.create_annotation(
            concept_ref=ConceptRef("phonetics@0.1:a"), geometry=PointGeometry(start_sample=0)
        )
    annotations = session.annotations
    session.save()
    recording_id = session.recording_id
    session.close()
    reopened = api.open_recording(recording_id)
    assert reopened.annotations == annotations
    assert reopened.pinned_libraries == versions
    reopened.close()


def test_all_bundles_round_trip(tmp_path):
    exercise_bundles(make_api(MemoryPersistenceStore(), tmp_path), tmp_path)


@pytest.mark.parametrize(
    "damage",
    [
        "namespace",
        "version",
        "duplicate",
        "geometry",
        "schema",
        "category",
        "metadata",
        "generated_id",
        "descriptor",
    ],
)
def test_invalid_bundles_rejected(tmp_path, damage):
    path = tmp_path / "phonetics" / "0.1"
    shutil.copytree(BUNDLE_ROOT / "phonetics" / "0.1", path)
    file = path / "definitions.json"
    data = json.loads(file.read_text("utf-8"))
    if damage in {"namespace", "version"}:
        file = path / "manifest.json"
        data = json.loads(file.read_text("utf-8"))
        data[damage] = "wrong"
    elif damage == "duplicate":
        data.append(data[0])
    elif damage == "geometry":
        data[0]["allowed_geometry_types"] = ["rectangle"]
    elif damage == "schema":
        data[0]["attribute_schema"] = {"type": "nonexistent"}
    elif damage == "category":
        data[0]["metadata"]["category"] = "missing"
    elif damage == "metadata":
        data[0]["metadata"]["aliases"] = 123
    elif damage == "generated_id":
        data[0]["id"] = str(uuid4())
    else:
        file = path / "categories.json"
        data = {"vowel": {"name": 123}}
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WorkbenchStartupError, match="Invalid bundled library"):
        load_bundle(path)


def test_category_content_is_hashed_and_paths_ignore_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = load_bundle(BUNDLE_ROOT / "phonetics" / "0.1")[1]
    path = tmp_path / "phonetics" / "0.1"
    shutil.copytree(BUNDLE_ROOT / "phonetics" / "0.1", path)
    file = path / "categories.json"
    data = json.loads(file.read_text("utf-8"))
    data["vowel"]["description"] = "Changed descriptor"
    file.write_text(json.dumps(data), encoding="utf-8")
    assert load_bundle(path)[1].content_sha256 != original.content_sha256


def test_placeholder_is_preserved_with_actionable_error(tmp_path):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    library, version = load_bundle(BUNDLE_ROOT / "core" / "0.1")
    api.libraries.create(library)
    placeholder = LibraryEntry(
        id=uuid4(),
        library_version_id=version.id,
        entry_key="test",
        display_name="Test annotation",
        description="Old placeholder",
        allowed_geometry_types=("time_interval",),
    )
    version = version.model_copy(update={"entries": (placeholder,)})
    version = version.model_copy(update={"content_sha256": library_content_sha256(version)})
    api.libraries.publish_version(version)
    with pytest.raises(WorkbenchStartupError, match="old test-only placeholder.*fresh development"):
        api.ensure_bundled_libraries()
    assert tuple(store.versions.values()) == (version,)
    assert tuple(store.libraries.values()) == (library,)


@pytest.mark.parametrize("stage", ["create_library", "publish_library_version"])
def test_all_bundles_verify_competing_winners(tmp_path, monkeypatch, stage):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    original = getattr(store, stage)

    def race(value):
        original(value)
        raise PersistenceIntegrityError("competing initializer won")

    monkeypatch.setattr(store, stage, race)
    versions = api.ensure_bundled_libraries()
    assert len(versions) == len(store.versions) == len(store.libraries) == 3
    assert all(v.content_sha256 == library_content_sha256(v) for v in versions)


def test_all_bundles_preflight_before_publication(tmp_path, monkeypatch):
    import application.core_library as initializer

    root = tmp_path / "libraries"
    shutil.copytree(BUNDLE_ROOT, root)
    (root / "prosody" / "0.1" / "definitions.json").write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(initializer, "BUNDLE_ROOT", root)
    store = MemoryPersistenceStore()
    with pytest.raises(WorkbenchStartupError, match="Invalid bundled library"):
        make_api(store, tmp_path).ensure_bundled_libraries()
    assert not store.libraries and not store.versions
