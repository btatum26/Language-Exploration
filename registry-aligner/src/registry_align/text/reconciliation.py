"""Backend/source word-label reconciliation."""

from __future__ import annotations

from registry_align.domain.transcripts import NormalizedTranscript


def source_token_ids_for_word(
    transcript: NormalizedTranscript, backend_word_index: int
) -> tuple[str, ...]:
    if backend_word_index < 0 or backend_word_index >= len(transcript.token_mappings):
        return ()
    return transcript.token_mappings[backend_word_index].source_token_ids
