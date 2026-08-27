"""Deterministic replay of immutable segment revisions into an effective topology."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from registry_align.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class EffectiveTopology:
    segments: tuple[dict[str, Any], ...]
    version: int

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {str(item["id"]): item for item in self.segments}


def replay_effective_topology(
    model_segments: list[dict[str, Any]], revisions: list[dict[str, Any]]
) -> EffectiveTopology:
    """Replay accepted revisions without mutating model rows."""

    effective: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source in sorted(
        model_segments,
        key=lambda item: (str(item.get("kind", "")), int(item["start_sample"]), str(item["id"])),
    ):
        item = dict(source)
        identifier = str(item["id"])
        item["id"] = identifier
        item["model_segment_id"] = identifier
        item["model_start_sample"] = item["start_sample"]
        item["model_end_sample"] = item["end_sample"]
        item["model_label"] = item["label"]
        item["effective_revision_id"] = None
        effective[identifier] = item
        order.append(identifier)

    version = 0
    ordered_revisions = sorted(revisions, key=lambda item: (item["created_at"], str(item["id"])))
    for revision in ordered_revisions:
        targets = _target_ids(revision, effective)
        _validate_operation(effective, targets, revision)
        if revision.get("review_state") != "accepted":
            continue
        _apply_operation(effective, order, targets, revision)
        version += 1

    output = tuple(
        sorted(
            effective.values(),
            key=lambda item: (
                int(item["start_sample"]),
                str(item.get("kind", "")),
                str(item["id"]),
            ),
        )
    )
    return EffectiveTopology(output, version)


def validate_candidate_revision(
    topology: EffectiveTopology, revision: dict[str, Any]
) -> tuple[str, ...]:
    effective = topology.by_id()
    targets = _target_ids(revision, effective)
    _validate_operation(effective, targets, revision)
    return targets


def _target_ids(revision: dict[str, Any], effective: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    raw = (
        revision.get("effective_target_segment_ids")
        or revision.get("affected_segment_ids")
        or (str(revision["segment_id"]),)
    )
    targets: list[str] = []
    for value in raw:
        identifier = str(value)
        if identifier in effective:
            targets.append(identifier)
            continue
        legacy = [
            key
            for key, item in effective.items()
            if str(item.get("model_segment_id")) == identifier
        ]
        if len(legacy) == 1:
            targets.append(legacy[0])
            continue
        if len(legacy) > 1:
            raise ConfigurationError(
                f"revision target {identifier} is stale after a topology split; reload and retry"
            )
        raise ConfigurationError(
            f"revision target {identifier} is missing or has already been absorbed; "
            "reload and retry"
        )
    if len(set(targets)) != len(targets):
        raise ConfigurationError("revision targets must be distinct")
    return tuple(targets)


def _validate_operation(
    effective: dict[str, dict[str, Any]],
    targets: tuple[str, ...],
    revision: dict[str, Any],
) -> None:
    operation = str(revision.get("operation") or "update")
    expected = 2 if operation == "merge" else 1
    if len(targets) != expected:
        raise ConfigurationError(f"{operation} requires {expected} effective segment target(s)")
    sources = [effective[item] for item in targets]
    replacements = list(revision.get("replacement_segments") or [])
    if operation == "update":
        if replacements:
            raise ConfigurationError("update revisions cannot contain replacements")
        start = revision.get("start_sample_override")
        end = revision.get("end_sample_override")
        start = sources[0]["start_sample"] if start is None else int(start)
        end = sources[0]["end_sample"] if end is None else int(end)
        if end <= start:
            raise ConfigurationError("revision end must be greater than its start")
        parent_id = _parent(sources[0])
        if parent_id is not None:
            parents = [
                item
                for identifier, item in effective.items()
                if identifier == parent_id or str(item.get("model_segment_id")) == parent_id
            ]
            if len(parents) > 1:
                raise ConfigurationError(
                    "revision parent is stale or ambiguous; reload the parent topology"
                )
            if parents:
                parent = parents[0]
                if start < int(parent["start_sample"]) or end > int(parent["end_sample"]):
                    raise ConfigurationError(
                        "segment boundaries must remain inside the current parent"
                    )
            for identifier, other in effective.items():
                if identifier == targets[0]:
                    continue
                if other.get("kind") != sources[0].get("kind") or _parent(other) != parent_id:
                    continue
                if start < int(other["end_sample"]) and end > int(other["start_sample"]):
                    raise ConfigurationError(
                        "segment boundaries overlap another effective segment in the same tier"
                    )
        return
    if operation == "split":
        if len(replacements) != 2:
            raise ConfigurationError("split revisions require two replacements")
        source = sources[0]
        first, second = sorted(replacements, key=lambda item: int(item["start_sample"]))
        if (
            int(first["start_sample"]) != int(source["start_sample"])
            or int(first["end_sample"]) != int(second["start_sample"])
            or int(second["end_sample"]) != int(source["end_sample"])
        ):
            raise ConfigurationError(
                "split replacements must exactly partition the effective segment"
            )
        if any(item.get("parent_segment_id") != _parent(source) for item in replacements):
            raise ConfigurationError("split replacements must preserve the effective parent")
        _validate_replacement_ids(effective, targets, replacements)
        return
    if operation != "merge":
        raise ConfigurationError(f"unsupported revision operation: {operation}")
    left, right = sorted(sources, key=lambda item: int(item["start_sample"]))
    if (
        left.get("kind") != right.get("kind")
        or _parent(left) != _parent(right)
        or int(left["end_sample"]) != int(right["start_sample"])
    ):
        raise ConfigurationError("merge targets must be adjacent in the same tier and parent")
    if len(replacements) != 1:
        raise ConfigurationError("merge revisions require one replacement")
    replacement = replacements[0]
    if (
        int(replacement["start_sample"]) != int(left["start_sample"])
        or int(replacement["end_sample"]) != int(right["end_sample"])
        or replacement.get("parent_segment_id") != _parent(left)
    ):
        raise ConfigurationError("merge replacement must exactly cover both effective segments")
    _validate_replacement_ids(effective, targets, replacements)


def _validate_replacement_ids(
    effective: dict[str, dict[str, Any]],
    targets: tuple[str, ...],
    replacements: list[dict[str, Any]],
) -> None:
    identifiers = [str(item["segment_id"]) for item in replacements]
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("replacement segment IDs must be unique")
    conflicts = set(identifiers) & (set(effective) - set(targets))
    if conflicts:
        raise ConfigurationError(f"replacement segment ID already exists: {sorted(conflicts)[0]}")


def _apply_operation(
    effective: dict[str, dict[str, Any]],
    order: list[str],
    targets: tuple[str, ...],
    revision: dict[str, Any],
) -> None:
    operation = str(revision.get("operation") or "update")
    revision_id = str(revision["id"])
    if operation == "update":
        target = targets[0]
        item = dict(effective[target])
        for source, destination in (
            ("start_sample_override", "start_sample"),
            ("end_sample_override", "end_sample"),
            ("label_override", "label"),
        ):
            if revision.get(source) is not None:
                item[destination] = revision[source]
        item["review_state"] = "accepted"
        item["effective_revision_id"] = revision_id
        effective[target] = item
        return

    sources = [effective.pop(target) for target in targets]
    anchor = str(sources[0]["model_segment_id"])
    first_index = min(order.index(target) for target in targets)
    order[:] = [item for item in order if item not in targets]
    replacements: list[str] = []
    template = min(sources, key=lambda item: int(item["start_sample"]))
    for value in revision.get("replacement_segments") or []:
        item = dict(template)
        identifier = str(value["segment_id"])
        item.update(
            id=identifier,
            label=value["label"],
            start_sample=int(value["start_sample"]),
            end_sample=int(value["end_sample"]),
            parent_segment_id=value.get("parent_segment_id"),
            model_segment_id=anchor,
            review_state="accepted",
            effective_revision_id=revision_id,
        )
        effective[identifier] = item
        replacements.append(identifier)
    order[first_index:first_index] = replacements


def _parent(item: dict[str, Any]) -> str | None:
    value = item.get("parent_segment_id")
    return str(value) if value is not None else None
