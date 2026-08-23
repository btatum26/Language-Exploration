from pathlib import Path

import pytest

from registry_align.registry.paths import AudioPathError, resolve_audio_path


@pytest.mark.parametrize(
    ("raw_path", "code"),
    [
        ("C:\\audio\\one.wav", "AUDIO_PATH_ABSOLUTE"),
        ("/audio/one.wav", "AUDIO_PATH_ABSOLUTE"),
        ("\\\\server\\share\\one.wav", "AUDIO_PATH_ABSOLUTE"),
        ("../one.wav", "AUDIO_PATH_TRAVERSAL"),
        ("audio/../one.wav", "AUDIO_PATH_TRAVERSAL"),
        ("", "AUDIO_PATH_REQUIRED"),
    ],
)
def test_rejects_unsafe_paths(tmp_path: Path, raw_path: str, code: str) -> None:
    with pytest.raises(AudioPathError) as caught:
        resolve_audio_path(tmp_path, raw_path)

    assert caught.value.code == code


def test_accepts_windows_separators_and_stores_portable_path(tmp_path: Path) -> None:
    audio = tmp_path / "audio" / "one.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")

    result = resolve_audio_path(tmp_path, "audio\\one.wav")

    assert result.portable_path == "audio/one.wav"
    assert result.resolved_path == audio.resolve()


def test_rejects_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(AudioPathError) as missing:
        resolve_audio_path(tmp_path, "audio/missing.wav")
    assert missing.value.code == "AUDIO_FILE_MISSING"

    directory = tmp_path / "audio"
    directory.mkdir()
    with pytest.raises(AudioPathError) as not_file:
        resolve_audio_path(tmp_path, "audio")
    assert not_file.value.code == "AUDIO_PATH_NOT_FILE"


def test_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"audio")
    link = root / "linked.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available to this Windows user")

    with pytest.raises(AudioPathError) as caught:
        resolve_audio_path(root, "linked.wav")

    assert caught.value.code == "AUDIO_PATH_ESCAPE"
