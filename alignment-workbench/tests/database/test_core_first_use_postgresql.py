"""First-use coverage with migrated disposable PostgreSQL schemas and real commits."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from PySide6 import QtWidgets
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from application.core_library import core_publication
from application.errors import WorkbenchStartupError
from application.handlers import LibraryHandler
from application.validation import library_content_sha256
from gui.main_window import MainWindow
from models import Library
from persistence.sqlalchemy.unit_of_work import SqlAlchemyPersistence
from tests.test_core_library import make_api
from tests.test_first_use_gui import exercise_first_use
from tests.test_gui import ImmediateRunner

pytestmark = pytest.mark.postgresql


@pytest.fixture
def core_store(database_tunnel, monkeypatch):
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is required for first-use PostgreSQL tests")
    # Never migrate or delete the configured production schema. Each test owns
    # this generated, isolated schema and its separate Alembic version table.
    schema = f"core_first_use_{uuid4().hex[:12]}_test"
    monkeypatch.setenv("REGISTRY_ALIGN_DATABASE_ADMIN_URL", test_url)
    config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
    config.attributes["snapshot_schema"] = schema
    config.attributes["manage_ssh_tunnel"] = False
    command.upgrade(config, "head")
    engine = create_engine(
        test_url,
        execution_options={
            "schema_translate_map": {"registry_align": schema},
        },
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield SqlAlchemyPersistence(factory), factory
    finally:
        engine.dispose()
        command.downgrade(config, "base")
        cleanup = create_engine(test_url)
        try:
            table = cleanup.dialect.identifier_preparer.quote(f"{schema}_alembic_version")
            with cleanup.begin() as connection:
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS public.{table}")
        finally:
            cleanup.dispose()


def test_empty_database_gui_save_restart_and_reopen(core_store, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *a, **kw: QtWidgets.QMessageBox.StandardButton.Discard,
    )
    store, factory = core_store
    assert store.list_libraries() == ()
    api = make_api(store, tmp_path)
    recording_id, annotation, core = exercise_first_use(qtbot, api, tmp_path, monkeypatch)
    store.close()
    # Fresh API, caches and persistence facade after closing the first window.
    restarted_store = SqlAlchemyPersistence(factory)
    restarted = make_api(restarted_store, tmp_path)
    assert restarted.ensure_core_library() == core
    assert len(restarted.libraries.list()) == 1
    assert restarted.libraries.list_versions(core.library_id) == (core,)
    window = MainWindow(restarted, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.show()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    qtbot.waitUntil(lambda: window.waveform._envelope is not None)
    reopened = window.controller.session
    assert reopened.annotations == (annotation,)
    assert reopened.pinned_libraries == (core,)
    snapshot = restarted_store.load_snapshot(recording_id)
    (pin,) = snapshot.libraries
    assert (pin.namespace, pin.version, pin.content_sha256) == ("core", "0.1", core.content_sha256)
    assert window.annotation_table.rowCount() == 1
    window.annotation_table.selectRow(0)
    assert window.note_edit.text() == annotation.note
    assert annotation.label == "Edited interval"
    assert window.label_edit.text() == "Edited interval"
    assert window.annotation_table.item(0, 6).text() == "Edited interval"
    assert window.end_sample.value() == annotation.geometry.end_sample
    assert "core@0.1" in window.concept_combo.currentText()
    window.close()


@pytest.mark.parametrize("with_later_version", [False, True])
def test_existing_identity_missing_exact_version(core_store, tmp_path, with_later_version):
    store, _ = core_store
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    later = core_publication(library.id).model_copy(update={"version_label": "0.2", "entries": ()})
    later = later.model_copy(update={"content_sha256": library_content_sha256(later)})
    if with_later_version:
        api.libraries.publish_version(later)
    core = api.ensure_core_library()
    assert core.library_id == library.id
    assert core.version_label == "0.1"
    assert api.ensure_core_library() == core
    if with_later_version:
        assert api.libraries.get_version(later.id) == later
    assert len(api.libraries.list_versions(library.id)) == (2 if with_later_version else 1)


def test_identical_content_under_other_label_is_preserved(core_store, tmp_path):
    store, _ = core_store
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    other = core_publication(library.id).model_copy(update={"version_label": "0.2"})
    api.libraries.publish_version(other)
    with pytest.raises(WorkbenchStartupError, match="already exists as core@0.2"):
        api.ensure_core_library()
    assert api.libraries.list_versions(library.id) == (other,)


def test_conflicting_content_fails_without_overwrite(core_store, tmp_path):
    store, _ = core_store
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    version = core_publication(library.id).model_copy(update={"entries": ()})
    version = version.model_copy(update={"content_sha256": library_content_sha256(version)})
    api.libraries.publish_version(version)
    with pytest.raises(WorkbenchStartupError, match="core@0.1 conflicts"):
        api.ensure_core_library()
    assert api.libraries.list_versions(library.id) == (version,)


def test_two_initializers_race_on_identity_and_publication(core_store, tmp_path, monkeypatch):
    _, factory = core_store
    create_barrier = Barrier(2)
    publish_barrier = Barrier(2)
    create = LibraryHandler.create
    publish = LibraryHandler.publish_version

    def simultaneous_create(self, library):
        create_barrier.wait(timeout=10)
        return create(self, library)

    def simultaneous_publish(self, version):
        publish_barrier.wait(timeout=10)
        return publish(self, version)

    monkeypatch.setattr(LibraryHandler, "create", simultaneous_create)
    monkeypatch.setattr(LibraryHandler, "publish_version", simultaneous_publish)

    def initialize(index):
        api = make_api(SqlAlchemyPersistence(factory), tmp_path / str(index))
        return api.ensure_core_library()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, (0, 1)))
    assert results[0] == results[1]
    api = make_api(SqlAlchemyPersistence(factory), tmp_path)
    assert len(api.libraries.list()) == 1
    assert api.libraries.list_versions(results[0].library_id) == (results[0],)


def test_all_bundles_persist_metadata_pins_and_annotations(core_store, tmp_path):
    from tests.test_bundled_libraries import exercise_bundles

    store, factory = core_store
    api = make_api(store, tmp_path)
    exercise_bundles(api, tmp_path)
    expected = api.ensure_bundled_libraries()
    restarted = make_api(SqlAlchemyPersistence(factory), tmp_path)
    assert restarted.ensure_bundled_libraries() == expected
