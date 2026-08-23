import pytest

from registry_align.registry.mapping import MISSING, get_property


def test_gets_only_object_properties() -> None:
    document = {"audio": {"path": "audio/one.wav"}}

    assert get_property(document, "audio.path") == "audio/one.wav"
    assert get_property(document, "audio.missing") is MISSING
    assert get_property([document], "$", allow_root=True) == [document]


@pytest.mark.parametrize("path", ["items[0]", "$", "a..b", ".a", "a."])
def test_rejects_unsafe_non_root_paths(path: str) -> None:
    with pytest.raises(ValueError):
        get_property({}, path)
