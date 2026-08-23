"""Deterministic Unicode word tokenization."""

import re

TOKEN_PATTERN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in TOKEN_PATTERN.finditer(text))
