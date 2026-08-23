"""Canonical registry entry models."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    id: str = Field(min_length=1)
    source_entry_index: int = Field(ge=0)
    source_registry_path: Path
    audio_relative_path: str = Field(min_length=1)
    audio_resolved_path: Path
    transcript_raw: str = Field(min_length=1)
    language: str = Field(min_length=1)
    speaker_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    id_was_derived: bool = False
