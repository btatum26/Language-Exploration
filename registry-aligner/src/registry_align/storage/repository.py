"""Repository protocol and stored cache record."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheRecord:
    cache_key: str
    recording_id: str
    result_run_id: str
    status: str
    canonical_pcm_path: Path
    canonical_pcm_sha256: str
    alignment_audio_path: Path
    alignment_audio_sha256: str
    backend_fingerprint: str
