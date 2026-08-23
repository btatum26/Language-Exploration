"""Build a registry-specific Italian MFA dictionary from the CV base dictionary."""

from __future__ import annotations

import importlib
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from registry_align.config import AppConfig
from registry_align.errors import DependencyError, ProcessingError, RegistryReadError
from registry_align.registry.loader import load_registry
from registry_align.text.normalizer import normalize_transcript


class PhoneTranscriber(Protocol):
    def trans_list(self, word: str) -> list[str]: ...


@dataclass(frozen=True)
class DictionaryBuildResult:
    output_path: Path
    vocabulary_words: int
    added_words: int
    added_pronunciations: int


def _strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _load_transcriber() -> PhoneTranscriber:
    try:
        epitran = importlib.import_module("epitran")
        constructor = epitran.Epitran
        return cast(PhoneTranscriber, constructor("ita-Latn"))
    except (ImportError, AttributeError, OSError) as exc:
        raise DependencyError(
            "Italian dictionary extension requires Epitran; install the Pixi environment"
        ) from exc


def _read_base_dictionary(path: Path) -> tuple[str, dict[str, tuple[str, ...]], set[str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryReadError(f"cannot read base MFA dictionary {path}: {exc}") from exc
    pronunciations: dict[str, list[str]] = {}
    phones: set[str] = set()
    for line_number, line in enumerate(source.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            word, pronunciation = line.split("\t", 1)
        except ValueError as exc:
            raise RegistryReadError(
                f"invalid base MFA dictionary line {line_number}: expected tab separator"
            ) from exc
        normalized_pronunciation = " ".join(pronunciation.split())
        if not normalized_pronunciation:
            raise RegistryReadError(
                f"invalid base MFA dictionary line {line_number}: pronunciation is empty"
            )
        pronunciations.setdefault(word.casefold(), []).append(normalized_pronunciation)
        phones.update(normalized_pronunciation.split())
    if not pronunciations:
        raise RegistryReadError(f"base MFA dictionary is empty: {path}")
    return source, {word: tuple(values) for word, values in pronunciations.items()}, phones


def _generated_pronunciations(
    word: str,
    base_pronunciations: dict[str, tuple[str, ...]],
    phones: set[str],
    transcriber: PhoneTranscriber,
) -> tuple[str, ...]:
    plain_word = _strip_diacritics(word).casefold()
    if plain_word != word.casefold() and plain_word in base_pronunciations:
        return base_pronunciations[plain_word]
    generated_phones = tuple(
        phone
        for phone in transcriber.trans_list(word)
        if phone and not all(unicodedata.category(character).startswith("P") for character in phone)
    )
    if not generated_phones:
        raise ProcessingError(f"Epitran produced no pronunciation for Italian word {word!r}")
    unsupported = sorted(set(generated_phones) - phones)
    if unsupported:
        raise ProcessingError(
            f"Epitran produced unsupported MFA phone(s) for {word!r}: {unsupported}"
        )
    return (" ".join(generated_phones),)


def build_italian_dictionary(
    registry_path: Path,
    base_dictionary: Path,
    output_path: Path,
    config: AppConfig,
    *,
    transcriber: PhoneTranscriber | None = None,
) -> DictionaryBuildResult:
    """Merge the base dictionary with pronunciations needed by one Italian registry."""

    base = base_dictionary.resolve(strict=False)
    output = output_path.resolve(strict=False)
    if base == output:
        raise ProcessingError("extended dictionary output cannot overwrite the base dictionary")
    validation = load_registry(
        registry_path,
        mapping=config.input,
        defaults=config.defaults,
        fail_fast=False,
    )
    if not validation.is_valid:
        raise RegistryReadError(
            f"cannot build dictionary from invalid registry: {validation.error_count} error(s)"
        )
    non_italian = sorted(
        {
            entry.language
            for entry in validation.entries
            if not entry.language.casefold().startswith("it")
        }
    )
    if non_italian:
        raise ProcessingError(
            f"Italian dictionary builder received non-Italian language(s): {non_italian}"
        )
    vocabulary = {
        token.text.casefold()
        for entry in validation.entries
        for token in normalize_transcript(
            entry.id,
            entry.transcript_raw,
            profile=config.text.normalizer,
            lowercase=config.text.lowercase,
        ).alignment_tokens
    }
    source, base_pronunciations, phones = _read_base_dictionary(base)
    missing_words = sorted(vocabulary - set(base_pronunciations))
    effective_transcriber = transcriber or _load_transcriber()
    additions: list[str] = []
    for word in missing_words:
        for pronunciation in _generated_pronunciations(
            word, base_pronunciations, phones, effective_transcriber
        ):
            additions.append(f"{word}\t{pronunciation}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(source.rstrip("\r\n"))
        temporary.write("\n")
        if additions:
            temporary.write("\n".join(additions))
            temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)
    return DictionaryBuildResult(
        output_path=output,
        vocabulary_words=len(vocabulary),
        added_words=len(missing_words),
        added_pronunciations=len(additions),
    )
