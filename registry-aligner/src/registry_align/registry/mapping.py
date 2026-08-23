"""Safe JSON object-property mapping."""

from __future__ import annotations

import re
from typing import Any

_PROPERTY_SEGMENT = re.compile(r"^[^.$\[\]\x00-\x1f]+$")
MISSING = object()


def validate_property_path(path: str, *, allow_root: bool = False) -> None:
    if allow_root and path == "$":
        return
    if not path or path == "$":
        raise ValueError("property path must not be empty or '$'")
    for segment in path.split("."):
        if not segment or not _PROPERTY_SEGMENT.fullmatch(segment):
            raise ValueError(f"unsafe property path {path!r}")


def get_property(data: object, path: str, *, allow_root: bool = False) -> Any:
    validate_property_path(path, allow_root=allow_root)
    if path == "$":
        return data
    current = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return MISSING
        current = current[segment]
    return current
