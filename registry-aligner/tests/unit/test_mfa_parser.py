import json
from pathlib import Path

from registry_align.alignment.mfa.parser import parse_results
from registry_align.alignment.mfa.staging import StagingEntry
from registry_align.alignment.models import AlignmentJob
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.text.normalizer import normalize_transcript


def test_parses_praatio_json_to_canonical_segments(tmp_path: Path) -> None:
    output = tmp_path / "output"
    path = output / "speaker" / "entry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "start": 0.0,
                "end": 1.00004,
                "tiers": {
                    "speaker - words": {
                        "type": "IntervalTier",
                        "entries": [[0.0, 0.1, ""], [0.1, 1.00004, "ciao"]],
                    },
                    "speaker - phones": {
                        "type": "IntervalTier",
                        "entries": [[0.1, 0.5, "tʃ"], [0.5, 1.00004, "ao"]],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    canonical = tmp_path / "canonical.wav"
    canonical.write_bytes(b"canonical")
    aligned = tmp_path / "aligned.wav"
    aligned.write_bytes(b"aligned")
    entry = RegistryEntry(
        id="one",
        source_entry_index=0,
        source_registry_path=tmp_path / "registry.json",
        audio_relative_path="source.wav",
        audio_resolved_path=source,
        transcript_raw="ciao",
        language="it",
    )
    prepared = PreparedRecording(
        recording_id="one",
        source_audio_sha256="a" * 64,
        source_codec="wav",
        source_sample_rate_hz=1000,
        source_channels=1,
        source_duration_s=1.0,
        canonical_pcm_path=canonical,
        canonical_pcm_sha256="b" * 64,
        canonical_sample_rate_hz=44100,
        canonical_channels=1,
        canonical_frame_count=44100,
        alignment_audio_path=aligned,
        alignment_audio_sha256="c" * 64,
        alignment_sample_rate_hz=1000,
        alignment_channels=1,
        decoder_name="fake",
        decoder_version="1",
    )
    transcript = normalize_transcript("one", "ciao", profile="italian")
    job = AlignmentJob(entry=entry, prepared=prepared, transcript=transcript)

    result = parse_results(
        output,
        (StagingEntry("one", "speaker/entry"),),
        (job,),
        "run_test",
        "italian",
    )["one"]

    assert [segment.tier for segment in result] == [
        "utterance",
        "silence",
        "word",
        "phone",
        "phone",
    ]
    assert result[2].source_token_ids == ("one:source-token:000000",)
    assert result[3].parent_segment_id == result[2].segment_id
    assert result[3].confidence is None
    assert result[-1].end_sample == 44100


def test_omits_recording_when_mfa_produces_no_output(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    prepared = PreparedRecording(
        recording_id="one",
        source_audio_sha256="a" * 64,
        source_codec="wav",
        source_sample_rate_hz=16000,
        source_channels=1,
        source_duration_s=1.0,
        canonical_pcm_path=source,
        canonical_pcm_sha256="b" * 64,
        canonical_sample_rate_hz=16000,
        canonical_channels=1,
        canonical_frame_count=16000,
        alignment_audio_path=source,
        alignment_audio_sha256="c" * 64,
        alignment_sample_rate_hz=16000,
        alignment_channels=1,
        decoder_name="fake",
        decoder_version="1",
    )
    entry = RegistryEntry(
        id="one",
        source_entry_index=0,
        source_registry_path=tmp_path / "registry.json",
        audio_relative_path="source.wav",
        audio_resolved_path=source,
        transcript_raw="ciao",
        language="it",
    )
    job = AlignmentJob(
        entry=entry,
        prepared=prepared,
        transcript=normalize_transcript("one", "ciao", profile="italian"),
    )

    result = parse_results(
        tmp_path / "missing-output",
        (StagingEntry("one", "speaker/entry"),),
        (job,),
        "run_test",
        "italian",
    )

    assert result == {}
