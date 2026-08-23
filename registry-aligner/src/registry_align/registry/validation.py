"""Aggregated registry validation results."""

from pydantic import BaseModel, ConfigDict

from registry_align.domain.entries import RegistryEntry
from registry_align.events import Issue, Severity


class RegistryValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[RegistryEntry, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(issue.severity == Severity.ERROR for issue in self.issues)
