"""Recording-level discovery. Sound discovery must query annotation intervals separately."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from uuid import UUID

from application import RecordingListItem
from models import RecordingMetadata

FIELDS = {
    "languages": "Language",
    "speakers": "Speaker",
    "collections": "Collection/session",
    "tags": "Tags",
}
UNSPECIFIED = "Unspecified"


def values(item: RecordingListItem, field: str) -> tuple[str, ...]:
    labels: tuple[str, ...] = getattr(item.metadata, field)
    if (
        not labels
        and item.metadata.legacy_labels
        and field == "languages"
        and item.language not in ("und", "")
    ):
        labels = (item.language.casefold(),)
    if (
        not labels
        and item.metadata.legacy_labels
        and field == "speakers"
        and item.speaker_display_name
    ):
        labels = (item.speaker_display_name.casefold(),)
    return labels or (UNSPECIFIED,)


def filtered(
    recordings: Iterable[RecordingListItem], query: str, filters: Mapping[str, set[str]], order: str
) -> list[RecordingListItem]:
    result = []
    for item in recordings:
        haystack = " ".join(
            (item.display_name, item.transcript, *values(item, "speakers"), *item.metadata.tags)
        ).casefold()
        if query.strip().casefold() not in haystack:
            continue
        if any(
            selected and not selected.intersection(values(item, field))
            for field, selected in filters.items()
        ):
            continue
        result.append(item)
    result.sort(key=lambda item: (item.display_name.casefold(), str(item.recording_id)))
    if order == "Duration":
        result.sort(key=lambda item: item.duration_seconds)
    elif order in ("Recently added", "Recently edited"):
        result.sort(
            key=lambda item: (
                (item.added_at or item.modified_at)
                if order == "Recently added"
                else item.modified_at
            ),
            reverse=True,
        )
    return result


def grouped(
    recordings: Iterable[RecordingListItem], field: str
) -> dict[str, list[RecordingListItem]]:
    groups: dict[str, list[RecordingListItem]] = defaultdict(list)
    for item in recordings:
        for label in values(item, field) if field else ("",):
            groups[label].append(item)
    return dict(
        sorted(groups.items(), key=lambda pair: (pair[0] == UNSPECIFIED, pair[0].casefold()))
    )


def selected_once(
    recordings: Iterable[RecordingListItem], selected: set[UUID]
) -> list[RecordingListItem]:
    return list(
        {item.recording_id: item for item in recordings if item.recording_id in selected}.values()
    )


def editable_metadata(item: RecordingListItem) -> RecordingMetadata:
    """Include legacy labels in the editor without changing annotation speaker references."""
    data = item.metadata.model_dump()
    for field in ("languages", "speakers"):
        data[field] = tuple(value for value in values(item, field) if value != UNSPECIFIED)
    return RecordingMetadata.model_validate(data)
