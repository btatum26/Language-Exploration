"""Deterministic fingerprints for immutable content and processing state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from registry_align.config import AppConfig
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.transcripts import NormalizedTranscript


def fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def config_fingerprint(config: AppConfig) -> str:
    return fingerprint(config.model_dump(mode="json"))


def content_fingerprint(
    entry: RegistryEntry,
    source_audio_sha256: str,
    transcript: NormalizedTranscript,
) -> str:
    return fingerprint(
        {
            "audio_sha256": source_audio_sha256,
            "raw_transcript_sha256": transcript.raw_sha256,
            "language": entry.language,
            "speaker_id": entry.speaker_id,
            "metadata": entry.metadata,
        }
    )


def processing_fingerprint(
    *,
    backend_fingerprint: str,
    normalizer_identity: str,
    audio_profile: str,
    pipeline_version: str,
) -> str:
    return fingerprint(
        {
            "backend": backend_fingerprint,
            "normalizer": normalizer_identity,
            "audio_profile": audio_profile,
            "pipeline_version": pipeline_version,
        }
    )
