from __future__ import annotations

import numpy as np

from spectrogram_playground.audio.decoding import track_from_samples
from spectrogram_playground.audio.engine import AudioEngine, FakeOutputBackend
from spectrogram_playground.model import Project, TransportState


def track(values: list[float], name: str = "track"):
    samples = np.asarray(values, dtype=np.float32)
    return track_from_samples(samples, 10, name=name, project_rate=10, analysis_rate=10)


def playing_project(*tracks) -> Project:
    project = Project(playback_rate=10)
    project.settings.analysis_rate = 10
    for item in tracks:
        project.add_track(item)
    project.transport.state = TransportState.PLAYING
    return project


def test_two_tracks_mix_to_expected_samples() -> None:
    from spectrogram_playground.audio.mixer import Mixer

    mixer = Mixer(playing_project(track([0.1, 0.2]), track([0.3, 0.4])))
    np.testing.assert_allclose(mixer.render(2), [0.4, 0.6])


def test_gain_mute_and_solo_change_mix() -> None:
    from spectrogram_playground.audio.mixer import Mixer

    first, second = track([0.2] * 4, "first"), track([0.3] * 4, "second")
    first.gain = 0.5
    project = playing_project(first, second)
    np.testing.assert_allclose(Mixer(project).render(1), [0.4])
    project.transport.frame_position = 0
    first.muted = True
    np.testing.assert_allclose(Mixer(project).render(1), [0.3])
    project.transport.frame_position = 0
    first.muted, first.solo = False, True
    np.testing.assert_allclose(Mixer(project).render(1), [0.1])


def test_short_track_is_silent_after_ending() -> None:
    from spectrogram_playground.audio.mixer import Mixer

    project = playing_project(track([0.5]), track([0.1, 0.2, 0.3]))
    np.testing.assert_allclose(Mixer(project).render(3), [0.6, 0.2, 0.3])


def test_seek_reads_expected_source_frame_and_advances_exactly() -> None:
    backend = FakeOutputBackend()
    project = playing_project(track([0, 0.1, 0.2, 0.3, 0.4]))
    project.transport.state = TransportState.STOPPED
    engine = AudioEngine(project, backend)
    engine.seek_seconds(0.2)
    engine.play()
    np.testing.assert_allclose(backend.pump(2), [0.2, 0.3])
    assert engine.frame_position == 4


def test_pause_resume_preserves_position() -> None:
    backend = FakeOutputBackend()
    project = playing_project(track([0.1] * 8))
    project.transport.state = TransportState.STOPPED
    engine = AudioEngine(project, backend)
    engine.play()
    backend.pump(3)
    engine.pause()
    assert engine.frame_position == 3
    np.testing.assert_array_equal(backend.pump(2), [0, 0])
    engine.play()
    backend.pump(2)
    assert engine.frame_position == 5


def test_visible_position_follows_dac_time_instead_of_queued_buffer_end() -> None:
    backend = FakeOutputBackend()
    project = playing_project(track([0.1] * 20))
    project.transport.state = TransportState.STOPPED
    engine = AudioEngine(project, backend)
    backend.clock_time = 5.0
    engine.play()

    backend.pump(4, dac_time=5.2)
    assert engine.frame_position == 4
    assert engine.current_time == 0.0

    backend.clock_time = 5.35
    assert np.isclose(engine.current_time, 0.15)

    backend.pump(4, dac_time=5.6)
    assert np.isclose(engine.current_time, 0.15)
    backend.clock_time = 5.75
    assert np.isclose(engine.current_time, 0.55)


def test_loop_wraps_inside_one_buffer() -> None:
    from spectrogram_playground.audio.mixer import Mixer

    project = playing_project(track([0, 0.1, 0.2, 0.3, 0.4, 0.5]))
    project.selection.set(0.1, 0.4, project.duration)
    project.transport.loop_selection = True
    project.transport.frame_position = 2
    np.testing.assert_allclose(Mixer(project).render(5), [0.2, 0.3, 0.1, 0.2, 0.3])
    assert project.transport.frame_position == 1


def test_mixer_rebases_a_trimmed_track_to_project_zero() -> None:
    from spectrogram_playground.audio.mixer import Mixer

    item = track([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    item.trim(0.2, 0.5)
    project = playing_project(item)
    np.testing.assert_allclose(Mixer(project).render(3), [0.2, 0.3, 0.4])
