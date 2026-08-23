"""Application request and result models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from registry_align.events import Issue


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_path: Path
    output_directory: Path
    resume: bool = True
    force: bool = False
    selected_ids: tuple[str, ...] = ()


class ProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: str
    counts: dict[str, int] = Field(default_factory=dict)
    issues: tuple[Issue, ...] = ()
    output_locations: dict[str, Path] = Field(default_factory=dict)


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recording_id: str
    status: str
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
