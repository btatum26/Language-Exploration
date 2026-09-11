"""Deterministic semantic annotation lanes, independent of zoom and clip placement."""

from collections import defaultdict
from collections.abc import Mapping
from uuid import UUID

from models import ConceptRef, LibraryEntry, SignalAnnotation


def annotation_lanes(
    annotations: tuple[SignalAnnotation, ...],
    definitions: Mapping[ConceptRef, LibraryEntry],
) -> tuple[dict[UUID, int], int]:
    rows: dict[UUID, int] = {}
    next_row = 0

    def ordered(items: list[SignalAnnotation]) -> list[SignalAnnotation]:
        return sorted(items, key=lambda a: (a.geometry.start_sample, str(a.id)))

    def end(annotation: SignalAnnotation) -> int:
        geometry = annotation.geometry
        # Coincident markers must remain individually selectable.
        return geometry.end_sample or geometry.start_sample + 1

    def fits(items: list[SignalAnnotation]) -> bool:
        previous_end = -1
        for annotation in ordered(items):
            if annotation.geometry.start_sample < previous_end:
                return False
            previous_end = end(annotation)
        return True

    def category(annotation: SignalAnnotation) -> str:
        entry = definitions.get(annotation.concept_ref)
        value = entry.metadata.get("category") if entry else None
        return value if isinstance(value, str) and value else annotation.concept_ref.entry_key

    def place(items: list[SignalAnnotation], depth: int) -> None:
        nonlocal next_row
        if fits(items):
            for annotation in items:
                rows[annotation.id] = next_row
            next_row += 1
            return
        if depth < 2:
            groups: dict[str, list[SignalAnnotation]] = defaultdict(list)
            for annotation in items:
                key = category(annotation) if depth == 0 else annotation.concept_ref.entry_key
                groups[key].append(annotation)
            for key in sorted(groups):
                place(groups[key], depth + 1)
            return
        # Only overlapping instances of the same definition reach this fallback.
        ends: list[int] = []
        for annotation in ordered(items):
            row = next(
                (i for i, stop in enumerate(ends) if stop <= annotation.geometry.start_sample),
                len(ends),
            )
            if row == len(ends):
                ends.append(end(annotation))
            else:
                ends[row] = end(annotation)
            rows[annotation.id] = next_row + row
        next_row += len(ends)

    libraries: dict[str, list[SignalAnnotation]] = defaultdict(list)
    for annotation in annotations:
        namespace = str(annotation.concept_ref).split("@", 1)[0]
        libraries[namespace].append(annotation)
    for namespace in sorted(libraries):
        place(libraries[namespace], 0)
    return rows, max(1, next_row)
