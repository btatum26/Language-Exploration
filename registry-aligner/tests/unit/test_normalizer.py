from registry_align.text.normalizer import normalize_transcript


def test_normalizer_preserves_raw_text_and_derives_alignment_text() -> None:
    raw = "  L’acqua — finí!  "

    result = normalize_transcript("one", raw, profile="italian")

    assert result.raw_text == raw
    assert result.normalized_text == "l'acqua finí"
    assert [token.text for token in result.source_tokens] == ["L'acqua", "finí"]
    assert [token.text for token in result.alignment_tokens] == ["l'acqua", "finí"]
    assert all(mapping.relationship == "one-to-one" for mapping in result.token_mappings)


def test_normalizer_warns_without_removing_bracketed_words() -> None:
    result = normalize_transcript("one", "ciao (molto piano)")

    assert result.normalized_text == "ciao molto piano"
    assert result.warnings == ("alphabetic content appears inside brackets or parentheses",)
