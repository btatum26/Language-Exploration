from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from registry_align.domain.segments import AlignmentSegment, SegmentRevision


def test_segment_uses_positive_half_open_interval() -> None:
    segment = AlignmentSegment(
        segment_id="one:phone:1",
        recording_id="one",
        tier="phone",
        label="i",
        backend_label="i",
        start_s=0.1,
        end_s=0.2,
        start_sample=100,
        end_sample=200,
        timebase_sample_rate_hz=1000,
        source="test",
        run_id="run_1",
        model_id="fake",
    )

    assert segment.confidence is None
    assert segment.start_sample == 100
    assert segment.end_sample == 200


def test_segment_rejects_reversed_interval() -> None:
    with pytest.raises(ValidationError):
        AlignmentSegment(
            segment_id="one:word:1",
            recording_id="one",
            tier="word",
            label="ciao",
            backend_label="ciao",
            start_s=0.2,
            end_s=0.1,
            start_sample=200,
            end_sample=100,
            timebase_sample_rate_hz=1000,
            source="test",
            run_id="run_1",
            model_id="fake",
        )


def test_revision_is_separate_from_model_segment() -> None:
    revision = SegmentRevision(
        revision_id="revision-1",
        segment_id="one:phone:1",
        base_run_id="run_1",
        start_sample_override=101,
        review_status="accepted",
        created_at=datetime.now(UTC),
    )

    assert revision.start_sample_override == 101
