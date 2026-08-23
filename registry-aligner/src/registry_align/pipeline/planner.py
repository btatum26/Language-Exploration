"""Side-effect-free planning over validated registry entries."""

from pathlib import Path

from registry_align.domain.runs import PlanItem, PlanResult
from registry_align.events import Issue, Severity
from registry_align.registry.validation import RegistryValidationResult


def build_plan(
    registry_path: Path,
    validation: RegistryValidationResult,
    selected_ids: tuple[str, ...] = (),
) -> PlanResult:
    selected = set(selected_ids)
    known_ids = {entry.id for entry in validation.entries}
    issues = list(validation.issues)
    for unknown_id in sorted(selected - known_ids):
        issues.append(
            Issue(
                code="SELECTED_ID_UNKNOWN",
                severity=Severity.ERROR,
                stage="plan",
                recording_id=unknown_id,
                message=f"selected recording ID does not exist: {unknown_id!r}",
                hint="Use an ID present in the validated registry.",
            )
        )

    items = tuple(
        PlanItem(
            recording_id=entry.id,
            status="new" if not selected or entry.id in selected else "ignored",
            audio_relative_path=entry.audio_relative_path,
            language=entry.language,
        )
        for entry in validation.entries
    )
    blocked_entry_indexes = {
        issue.details["source_entry_index"]
        for issue in issues
        if issue.severity == Severity.ERROR and "source_entry_index" in issue.details
    }
    has_global_error = any(
        issue.severity == Severity.ERROR and "source_entry_index" not in issue.details
        for issue in issues
    )
    counts = {
        "new": sum(item.status == "new" for item in items),
        "changed": 0,
        "cached": 0,
        "blocked": len(blocked_entry_indexes) + int(has_global_error),
        "ignored": sum(item.status == "ignored" for item in items),
        "selected": sum(item.status == "new" for item in items),
        "issues": len(issues),
    }
    return PlanResult(
        registry_path=registry_path.resolve(strict=False),
        items=items,
        issues=tuple(issues),
        counts=counts,
    )
