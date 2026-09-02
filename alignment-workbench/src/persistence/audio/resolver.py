"""Logical audio URI resolution boundary."""

from pathlib import Path
from typing import Protocol


class AudioResolver(Protocol):
    def resolve(self, storage_uri: str) -> Path: ...
