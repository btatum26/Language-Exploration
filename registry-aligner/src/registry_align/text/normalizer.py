"""Conservative raw-preserving transcript normalization."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from registry_align.domain.transcripts import NormalizedTranscript, Token, TokenMapping
from registry_align.text.tokenizer import tokenize

NORMALIZER_VERSION = "1"
BRACKETED_ALPHA = re.compile(r"[\[(][^\])]*[^\W\d_][^\])]*[\])]", re.UNICODE)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_transcript(
    recording_id: str,
    raw_text: str,
    *,
    profile: str = "conservative",
    lowercase: bool = True,
) -> NormalizedTranscript:
    warnings: list[str] = []
    if BRACKETED_ALPHA.search(raw_text):
        warnings.append("alphabetic content appears inside brackets or parentheses")
    normalized = unicodedata.normalize("NFC", raw_text)
    normalized = normalized.replace("’", "'").replace("‘", "'")
    normalized = normalized.replace("–", "-").replace("—", "-")
    if lowercase:
        normalized = normalized.lower()
    normalized_tokens = tokenize(normalized)
    normalized = " ".join(normalized_tokens)

    raw_tokens = tokenize(unicodedata.normalize("NFC", raw_text).replace("’", "'"))
    source_models = tuple(
        Token(token_id=f"{recording_id}:source-token:{index:06d}", text=text, index=index)
        for index, text in enumerate(raw_tokens)
    )
    alignment_models = tuple(
        Token(token_id=f"{recording_id}:alignment-token:{index:06d}", text=text, index=index)
        for index, text in enumerate(normalized_tokens)
    )
    mappings: list[TokenMapping] = []
    for index in range(max(len(source_models), len(alignment_models))):
        source_ids = (source_models[index].token_id,) if index < len(source_models) else ()
        alignment_ids = (alignment_models[index].token_id,) if index < len(alignment_models) else ()
        mappings.append(
            TokenMapping(
                source_token_ids=source_ids,
                alignment_token_ids=alignment_ids,
                relationship="one-to-one" if source_ids and alignment_ids else "unlinked",
            )
        )
    return NormalizedTranscript(
        recording_id=recording_id,
        raw_text=raw_text,
        normalized_text=normalized,
        raw_sha256=_sha256_text(raw_text),
        normalized_sha256=_sha256_text(normalized),
        normalizer_name=profile,
        normalizer_version=NORMALIZER_VERSION,
        source_tokens=source_models,
        alignment_tokens=alignment_models,
        token_mappings=tuple(mappings),
        warnings=tuple(warnings),
    )
