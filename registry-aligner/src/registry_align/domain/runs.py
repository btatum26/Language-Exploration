"""Application requests and results, independent of persistence."""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from registry_align.events import Issue


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_path: Path
    overwrite_existing: bool = False
    selected_ids: tuple[str, ...] = ()


class ProcessItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: str
    outcome: Literal[
        "skipped", "unchanged", "version-created", "alignment-reused", "processed", "failed"
    ]
    recording_version_id: UUID | None = None
    alignment_result_id: UUID | None = None
    detail: str | None = None


class ProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    status: Literal["complete", "partial", "failed"]
    counts: dict[str, int] = Field(default_factory=dict)
    items: tuple[ProcessItem, ...] = ()
    issues: tuple[Issue, ...] = ()


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: str
    status: Literal["new", "existing", "ignored", "blocked"]
    audio_relative_path: str
    language: str


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_path: Path
    items: tuple[PlanItem, ...]
    issues: tuple[Issue, ...]
    counts: dict[str, int]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)
