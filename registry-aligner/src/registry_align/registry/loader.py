"""Canonical and mapped registry loading without source mutation."""

from __future__ import annotations

import json
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from registry_align.config import DefaultsConfig, InputConfig
from registry_align.domain.entries import RegistryEntry
from registry_align.errors import RegistryReadError
from registry_align.events import Issue, Severity
from registry_align.registry.mapping import MISSING, get_property
from registry_align.registry.paths import AudioPathError, resolve_audio_path
from registry_align.registry.validation import RegistryValidationResult

DERIVED_ID_NAMESPACE = uuid.UUID("a1228a69-f774-5ca8-b3a1-211d40d1aa9e")


def _error(
    code: str,
    message: str,
    *,
    index: int | None = None,
    recording_id: str | None = None,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> Issue:
    issue_details = dict(details or {})
    if index is not None:
        issue_details["source_entry_index"] = index
    return Issue(
        code=code,
        severity=Severity.ERROR,
        stage="registry",
        recording_id=recording_id,
        message=message,
        hint=hint,
        details=issue_details,
    )


def _read_json(path: Path) -> object:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryReadError(f"cannot read registry {path}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryReadError(f"invalid UTF-8 JSON registry {path}: {exc}") from exc


def _string_value(
    source: dict[str, Any], path: str, field: str, index: int, issues: list[Issue]
) -> str | None:
    value = get_property(source, path)
    if value is MISSING or value is None:
        return None
    if not isinstance(value, str):
        issues.append(
            _error(
                f"{field.upper()}_TYPE",
                f"mapped {field} must be a string",
                index=index,
                details={"path": path},
            )
        )
        return None
    return value


def _document_language(document: object) -> str | None:
    if not isinstance(document, dict):
        return None
    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        return None
    language = defaults.get("language")
    return language if isinstance(language, str) and language.strip() else None


def load_registry(
    registry_path: Path,
    *,
    mapping: InputConfig | None = None,
    defaults: DefaultsConfig | None = None,
    fail_fast: bool = False,
) -> RegistryValidationResult:
    path = registry_path.resolve(strict=False)
    document = _read_json(path)
    adapter_mode = mapping is not None
    effective_mapping = mapping or InputConfig()
    effective_defaults = defaults or DefaultsConfig()
    document_issues: list[Issue] = []
    if not adapter_mode:
        if not isinstance(document, dict):
            return RegistryValidationResult(
                issues=(_error("REGISTRY_NOT_OBJECT", "canonical registry root must be an object"),)
            )
        if document.get("schema_version") != "1.0":
            document_issues.append(
                _error(
                    "SCHEMA_VERSION_UNSUPPORTED",
                    "canonical registry schema_version must be '1.0'",
                    hint="Migrate the registry or use a mapping configuration for another shape.",
                )
            )
        raw_defaults = document.get("defaults", {})
        if not isinstance(raw_defaults, dict):
            document_issues.append(
                _error("DEFAULTS_NOT_OBJECT", "canonical registry defaults must be an object")
            )
        elif "language" in raw_defaults and (
            not isinstance(raw_defaults["language"], str) or not raw_defaults["language"].strip()
        ):
            document_issues.append(
                _error(
                    "DEFAULT_LANGUAGE_INVALID",
                    "canonical registry default language must be a non-empty string",
                )
            )
        if fail_fast and document_issues:
            return RegistryValidationResult(issues=tuple(document_issues))
    entries_value = get_property(
        document, effective_mapping.entries_path, allow_root=effective_mapping.entries_path == "$"
    )
    if entries_value is MISSING:
        return RegistryValidationResult(
            issues=(
                _error(
                    "ENTRIES_PATH_MISSING",
                    f"entries path {effective_mapping.entries_path!r} does not exist",
                    hint="Set [input].entries_path to the JSON array containing recordings.",
                ),
            )
        )
    if not isinstance(entries_value, list):
        return RegistryValidationResult(
            issues=(
                _error(
                    "ENTRIES_NOT_ARRAY",
                    f"entries path {effective_mapping.entries_path!r} must resolve to an array",
                ),
            )
        )

    registry_root = path.parent
    registry_default_language = _document_language(document)
    entries: list[RegistryEntry] = []
    issues: list[Issue] = document_issues
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()

    for index, raw_entry in enumerate(entries_value):
        entry_issue_start = len(issues)
        if not isinstance(raw_entry, dict):
            issues.append(
                _error("ENTRY_NOT_OBJECT", "registry entry must be an object", index=index)
            )
            if fail_fast:
                break
            continue

        entry_id = _string_value(raw_entry, effective_mapping.id_path, "id", index, issues)
        audio_path = _string_value(
            raw_entry, effective_mapping.audio_path, "audio_path", index, issues
        )
        transcript = _string_value(
            raw_entry, effective_mapping.transcript_path, "transcript", index, issues
        )
        language = _string_value(
            raw_entry, effective_mapping.language_path, "language", index, issues
        )
        speaker = _string_value(
            raw_entry, effective_mapping.speaker_path, "speaker_id", index, issues
        )

        if audio_path is None or not audio_path.strip():
            issues.append(
                _error(
                    "AUDIO_PATH_REQUIRED",
                    "audio path is required",
                    index=index,
                    recording_id=entry_id,
                )
            )
        if transcript is None or not transcript.strip():
            issues.append(
                _error(
                    "TRANSCRIPT_REQUIRED",
                    "word-for-word transcript is required",
                    index=index,
                    recording_id=entry_id,
                )
            )
        resolved_language = language or registry_default_language or effective_defaults.language
        if resolved_language is None or not resolved_language.strip():
            issues.append(
                _error(
                    "LANGUAGE_REQUIRED",
                    "language must be supplied by the entry, registry defaults, or configuration",
                    index=index,
                    recording_id=entry_id,
                )
            )

        resolved_audio = None
        if audio_path is not None and audio_path.strip():
            try:
                resolved_audio = resolve_audio_path(registry_root, audio_path)
            except AudioPathError as exc:
                issues.append(
                    _error(
                        exc.code,
                        str(exc),
                        index=index,
                        recording_id=entry_id,
                        hint=exc.hint,
                    )
                )

        id_was_derived = False
        if entry_id is None or not entry_id.strip():
            if adapter_mode and resolved_audio is not None:
                entry_id = str(uuid.uuid5(DERIVED_ID_NAMESPACE, resolved_audio.normalized_key))
                id_was_derived = True
            else:
                issues.append(_error("ID_REQUIRED", "recording ID is required", index=index))
        elif entry_id != entry_id.strip():
            issues.append(
                _error(
                    "ID_WHITESPACE",
                    "recording ID cannot have leading or trailing whitespace",
                    index=index,
                    recording_id=entry_id,
                )
            )

        metadata: dict[str, Any] = {}
        if not adapter_mode:
            raw_metadata = raw_entry.get("metadata", {})
            if isinstance(raw_metadata, dict):
                metadata.update(raw_metadata)
            else:
                issues.append(
                    _error(
                        "METADATA_TYPE",
                        "metadata must be an object",
                        index=index,
                        recording_id=entry_id,
                    )
                )
            known = {
                "id",
                "audio_path",
                "transcript",
                "language",
                "speaker_id",
                "metadata",
            }
            metadata.update({key: value for key, value in raw_entry.items() if key not in known})
        else:
            for metadata_path in effective_mapping.metadata_paths:
                value = get_property(raw_entry, metadata_path)
                if value is not MISSING:
                    metadata[metadata_path.rsplit(".", 1)[-1]] = value

        if len(issues) > entry_issue_start:
            if fail_fast:
                break
            continue
        assert entry_id is not None
        assert transcript is not None
        assert resolved_language is not None
        assert resolved_audio is not None

        normalized_id = unicodedata.normalize("NFC", entry_id)
        is_duplicate = False
        if normalized_id in seen_ids:
            issues.append(
                _error(
                    "DUPLICATE_ID",
                    f"duplicate recording ID: {entry_id!r}",
                    index=index,
                    recording_id=entry_id,
                )
            )
            is_duplicate = True
        if resolved_audio.normalized_key in seen_paths:
            issues.append(
                _error(
                    "DUPLICATE_AUDIO_PATH",
                    f"duplicate normalized audio path: {resolved_audio.portable_path!r}",
                    index=index,
                    recording_id=entry_id,
                )
            )
            is_duplicate = True
        if is_duplicate:
            if fail_fast:
                break
            continue

        try:
            entry = RegistryEntry(
                id=entry_id,
                source_entry_index=index,
                source_registry_path=path,
                audio_relative_path=resolved_audio.portable_path,
                audio_resolved_path=resolved_audio.resolved_path,
                transcript_raw=transcript,
                language=resolved_language,
                speaker_id=speaker,
                metadata=metadata,
                id_was_derived=id_was_derived,
            )
        except ValidationError as exc:
            issues.append(
                _error(
                    "ENTRY_INVALID",
                    f"entry failed canonical validation: {exc}",
                    index=index,
                    recording_id=entry_id,
                )
            )
            if fail_fast:
                break
            continue
        seen_ids.add(normalized_id)
        seen_paths.add(resolved_audio.normalized_key)
        entries.append(entry)

    return RegistryValidationResult(entries=tuple(entries), issues=tuple(issues))
