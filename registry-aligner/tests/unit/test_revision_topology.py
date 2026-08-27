from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from registry_align.domain.topology import replay_effective_topology
from registry_align.errors import ConfigurationError


def model(identifier: str, start: int, end: int, label: str) -> dict[str, object]:
    return {
        "id": identifier,
        "kind": "phone",
        "label": label,
        "start_sample": start,
        "end_sample": end,
        "parent_segment_id": "word",
        "timebase_sample_rate_hz": 16_000,
    }


def revision(
    anchor: str,
    index: int,
    operation: str,
    targets: tuple[str, ...],
    replacements: tuple[dict[str, object], ...] = (),
    **overrides: object,
) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "segment_id": anchor,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        "review_state": "accepted",
        "operation": operation,
        "effective_target_segment_ids": targets,
        "replacement_segments": replacements,
        "start_sample_override": overrides.get("start"),
        "end_sample_override": overrides.get("end"),
        "label_override": overrides.get("label"),
    }


def replacement(identifier: str, start: int, end: int, label: str) -> dict[str, object]:
    return {
        "segment_id": identifier,
        "label": label,
        "start_sample": start,
        "end_sample": end,
        "parent_segment_id": "word",
    }


def test_split_reload_update_and_split_again_preserve_both_halves() -> None:
    source = model("a", 0, 100, "ab")
    revisions = [
        revision(
            "a",
            1,
            "split",
            ("a",),
            (replacement("left", 0, 40, "a"), replacement("right", 40, 100, "b")),
        ),
        revision("a", 2, "update", ("left",), label="A"),
        revision(
            "a",
            3,
            "split",
            ("right",),
            (replacement("mid", 40, 70, "b"), replacement("end", 70, 100, "c")),
        ),
    ]

    topology = replay_effective_topology([source], revisions)

    assert [(item["id"], item["label"]) for item in topology.segments] == [
        ("left", "A"),
        ("mid", "b"),
        ("end", "c"),
    ]
    assert sum(item["end_sample"] - item["start_sample"] for item in topology.segments) == 100


def test_merge_relabel_then_split_uses_merged_effective_id() -> None:
    revisions = [
        revision(
            "a",
            1,
            "merge",
            ("a", "b"),
            (replacement("merged", 0, 100, "ab"),),
        ),
        revision("a", 2, "update", ("merged",), label="AB"),
        revision(
            "a",
            3,
            "split",
            ("merged",),
            (replacement("new-left", 0, 60, "A"), replacement("new-right", 60, 100, "B")),
        ),
    ]

    topology = replay_effective_topology(
        [model("a", 0, 40, "a"), model("b", 40, 100, "b")], revisions
    )

    assert [
        (item["id"], item["start_sample"], item["end_sample"]) for item in topology.segments
    ] == [
        ("new-left", 0, 60),
        ("new-right", 60, 100),
    ]


def test_proposed_and_rejected_revisions_do_not_change_effective_topology() -> None:
    proposed = revision("a", 1, "update", ("a",), label="proposed")
    proposed["review_state"] = "proposed"
    rejected = revision("a", 2, "update", ("a",), label="rejected")
    rejected["review_state"] = "rejected"
    accepted = revision("a", 3, "update", ("a",), label="accepted")

    topology = replay_effective_topology(
        [model("a", 0, 100, "model")], [proposed, rejected, accepted]
    )

    assert topology.segments[0]["label"] == "accepted"
    assert topology.version == 1


def test_absorbed_or_stale_target_is_rejected_actionably() -> None:
    revisions = [
        revision(
            "a",
            1,
            "split",
            ("a",),
            (replacement("left", 0, 50, "a"), replacement("right", 50, 100, "b")),
        ),
        revision("a", 2, "update", ("a",), label="stale"),
    ]

    with pytest.raises(ConfigurationError, match="stale.*reload"):
        replay_effective_topology([model("a", 0, 100, "ab")], revisions)


def test_update_cannot_overlap_an_adjacent_effective_segment() -> None:
    overlapping = revision("a", 1, "update", ("a",), end=60)

    with pytest.raises(ConfigurationError, match="overlap"):
        replay_effective_topology(
            [model("a", 0, 50, "a"), model("b", 50, 100, "b")],
            [overlapping],
        )
