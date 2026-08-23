from pathlib import Path

import pytest

from registry_align.audio.probe import sha256_file
from registry_align.audio.timebase import seconds_to_sample


def test_sha256_file_streams_source_bytes(tmp_path: Path) -> None:
    path = tmp_path / "audio.bin"
    path.write_bytes(b"abc")

    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize(
    ("seconds", "rate", "sample"),
    [(0.0, 44100, 0), (0.5, 44100, 22050), (0.0005, 1000, 1), (0.0015, 1000, 2)],
)
def test_seconds_to_sample_uses_half_up_rounding(seconds: float, rate: int, sample: int) -> None:
    assert seconds_to_sample(seconds, rate) == sample


def test_seconds_to_sample_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        seconds_to_sample(-0.1, 44100)
    with pytest.raises(ValueError):
        seconds_to_sample(0.1, 0)
