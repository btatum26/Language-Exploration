"""Prepared-audio provenance model for later pipeline milestones."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PreparedRecording(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: str
    source_audio_sha256: str
    source_codec: str
    source_sample_rate_hz: int = Field(gt=0)
    source_channels: int = Field(gt=0)
    source_duration_s: float = Field(gt=0)
    source_frame_count: int | None = Field(default=None, gt=0)
    canonical_pcm_path: Path
    canonical_pcm_sha256: str
    canonical_sample_rate_hz: int = Field(gt=0)
    canonical_channels: int = Field(gt=0)
    canonical_frame_count: int = Field(gt=0)
    alignment_audio_path: Path
    alignment_audio_sha256: str
    alignment_sample_rate_hz: int = Field(gt=0)
    alignment_channels: int = Field(gt=0)
    decoder_name: str
    decoder_version: str
    preparation_fingerprint: str = "unspecified"
