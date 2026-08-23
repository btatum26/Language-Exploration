"""Backend-independent interval and revision models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AlignmentSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_id: str
    recording_id: str
    tier: str
    label: str
    backend_label: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    timebase_sample_rate_hz: int = Field(gt=0)
    parent_segment_id: str | None = None
    source_token_ids: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    review_status: str = "automatic"
    source: str
    run_id: str
    model_id: str
    notes: str | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "AlignmentSegment":
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be greater than start_s")
        if self.end_sample <= self.start_sample:
            raise ValueError("end_sample must be greater than start_sample")
        return self


class SegmentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    segment_id: str
    base_run_id: str
    start_sample_override: int | None = Field(default=None, ge=0)
    end_sample_override: int | None = Field(default=None, gt=0)
    label_override: str | None = None
    review_status: str
    author: str | None = None
    created_at: datetime
    reason: str | None = None
