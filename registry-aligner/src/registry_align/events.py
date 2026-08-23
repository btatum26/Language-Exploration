"""Interface-independent progress and issue events."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: Severity
    stage: str
    message: str
    hint: str | None = None
    recording_id: str | None = None
    run_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recording_id: str | None = None
    status: str | None = None
    issue: Issue | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EventObserver(Protocol):
    def on_event(self, event: Event) -> None: ...


class NullEventObserver:
    def on_event(self, event: Event) -> None:
        del event
