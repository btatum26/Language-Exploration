from uuid import uuid4

from gui.annotation_lanes import annotation_lanes
from gui.waveform import WaveformEnvelope, WaveformView
from models import ConceptRef, LibraryEntry, SignalAnnotation, TimeIntervalGeometry


def annotation(reference, start, end):
    return SignalAnnotation(
        id=uuid4(),
        concept_ref=ConceptRef(reference),
        geometry=TimeIntervalGeometry(start_sample=start, end_sample=end),
    )


def definition(reference, category):
    return LibraryEntry(
        id=uuid4(),
        library_version_id=uuid4(),
        entry_key=ConceptRef(reference).entry_key,
        display_name=category,
        description="",
        allowed_geometry_types=("time_interval",),
        metadata={"category": category},
    )


def test_libraries_have_distinct_rows_even_without_overlap():
    word = annotation("core@0.1:word", 0, 10)
    vowel = annotation("phonetics@0.1:a", 20, 30)
    consonant = annotation("phonetics@0.1:p", 40, 50)
    rows, count = annotation_lanes((consonant, word, vowel), {})
    assert count == 2
    assert rows[word.id] == 0
    assert rows[vowel.id] == rows[consonant.id] == 1


def test_overflow_groups_categories_then_definitions():
    word = annotation("core@0.1:word", 0, 100)
    second_word = annotation("core@0.1:word", 100, 200)
    syllable = annotation("core@0.1:syllable", 20, 60)
    a = annotation("phonetics@0.1:a", 0, 40)
    e = annotation("phonetics@0.1:e", 40, 80)
    p = annotation("phonetics@0.1:p", 20, 60)
    definitions = {
        a.concept_ref: definition(str(a.concept_ref), "vowel"),
        e.concept_ref: definition(str(e.concept_ref), "vowel"),
        p.concept_ref: definition(str(p.concept_ref), "consonant"),
    }
    annotations = (word, a, e, p, syllable, second_word)
    rows, count = annotation_lanes(annotations, definitions)
    assert count == 4
    assert rows[word.id] == rows[second_word.id] != rows[syllable.id]
    assert rows[a.id] == rows[e.id] != rows[p.id]
    assert max(rows[word.id], rows[syllable.id]) < min(rows[a.id], rows[p.id])
    overlapping_a = annotation("phonetics@0.1:a", 20, 50)
    split, count = annotation_lanes((*annotations, overlapping_a), definitions)
    assert count == 6
    assert len({split[a.id], split[e.id], split[overlapping_a.id]}) == 3
    assert annotation_lanes(tuple(reversed(annotations)), definitions) == (rows, 4)


def test_lane_assignment_does_not_change_with_zoom_or_offset(qtbot):
    view = WaveformView()
    qtbot.addWidget(view)
    annotations = (
        annotation("core@0.1:word", 0, 50),
        annotation("core@0.1:syllable", 20, 40),
        annotation("phonetics@0.1:a", 20, 40),
    )
    view.set_recording(WaveformEnvelope((0.0,), (0.0,), 1000, 100), annotations)
    initial = view.annotation_rows()
    view.set_viewport(10, 100)
    view.workspace_offset = 4.5
    view.set_viewport(-400, 300)
    assert view.annotation_rows() == initial
    view.show()
    view.grab()
    rectangles = list(view._annotation_rects.values())
    assert all(
        not left.intersects(right)
        for index, left in enumerate(rectangles)
        for right in rectangles[index + 1 :]
    )
