"""Stable pipeline cache fingerprints."""

import hashlib
import json

from registry_align.config import AppConfig
from registry_align.domain.transcripts import NormalizedTranscript

PIPELINE_VERSION = "1"


def config_fingerprint(config: AppConfig) -> str:
    payload = config.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def recording_cache_key(
    source_audio_sha256: str,
    transcript: NormalizedTranscript,
    config: AppConfig,
    ffmpeg_version: str,
    backend_fingerprint: str,
) -> str:
    payload = {
        "source_audio_sha256": source_audio_sha256,
        "raw_transcript_sha256": transcript.raw_sha256,
        "normalizer": {
            "name": transcript.normalizer_name,
            "version": transcript.normalizer_version,
            "config": config.text.model_dump(mode="json"),
        },
        "audio": config.audio.model_dump(mode="json"),
        "ffmpeg_version": ffmpeg_version,
        "backend_fingerprint": backend_fingerprint,
        "pipeline_version": PIPELINE_VERSION,
        "schema_version": config.schema_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
