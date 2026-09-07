from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.validation import library_content_sha256
from models import LibraryEntry, LibraryVersion
from scripts.migrate_core_placeholder import replacement_publication


def placeholder_publication(library_id=None):
    version_id = uuid4()
    version = LibraryVersion(
        id=version_id,
        library_id=library_id or uuid4(),
        version_label="0.1",
        content_sha256="0" * 64,
        created_at=datetime.now(UTC),
        entries=(
            LibraryEntry(
                id=uuid4(),
                library_version_id=version_id,
                entry_key="test",
                display_name="Test annotation",
                description="A general-purpose time interval for testing manual annotation.",
                allowed_geometry_types=("time_interval",),
                attribute_schema={"type": "object"},
            ),
        ),
    )
    return version.model_copy(update={"content_sha256": library_content_sha256(version)})


def test_transition_preserves_referenced_ids_and_version_metadata():
    current = placeholder_publication()
    assert (
        current.content_sha256 == "45e76abc06f37b5c1c488d694248ae64552ac6487d75fc4cfe6d475c8a75f161"
    )
    target = replacement_publication(current)
    assert (target.id, target.library_id, target.created_at, target.version_label) == (
        current.id,
        current.library_id,
        current.created_at,
        "0.1",
    )
    assert [e.entry_key for e in target.entries] == [
        "silence",
        "unknown",
        "word",
        "syllable",
        "noise",
        "breath",
        "marker",
    ]
    assert target.entries[2].id == current.entries[0].id
    assert all(e.library_version_id == current.id for e in target.entries)
    assert target.content_sha256 == library_content_sha256(target)
    assert replacement_publication(target) is target


@pytest.mark.parametrize("damage", ["hash", "definition", "extra", "version"])
def test_transition_refuses_non_placeholder_content(damage):
    current = placeholder_publication()
    if damage == "hash":
        current = current.model_copy(update={"content_sha256": "0" * 64})
    elif damage == "version":
        current = current.model_copy(update={"version_label": "0.2"})
    else:
        entries = (current.entries[0].model_copy(update={"description": "Different"}),)
        if damage == "extra":
            entries += current.entries
        current = current.model_copy(update={"entries": entries})
        current = current.model_copy(update={"content_sha256": library_content_sha256(current)})
    with pytest.raises(ValueError):
        replacement_publication(current)
