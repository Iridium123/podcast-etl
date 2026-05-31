"""Tests for eval.annotate: creating Labels files for hand correction."""

from __future__ import annotations

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.annotate import bootstrap_labels, create_blank


def _source_labels(annotator="claude-haiku-4-5-20251001"):
    return Labels(
        episode_ref=EpisodeRef(podcast_slug="pod", episode_json="ep.json"),
        audio_duration=120.0,
        segments=[AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Pre-roll")],
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": annotator, "prompt": "default"},
            annotator=annotator,
            created_at="2026-05-31T00:00:00",
        ),
    )


class TestCreateBlank:
    def test_empty_skeleton(self):
        ref = EpisodeRef(podcast_slug="pod", episode_json="ep.json")
        labels = create_blank(ref, audio_duration=300.0)

        assert labels.episode_ref == ref
        assert labels.audio_duration == 300.0
        assert labels.segments == []
        assert labels.provenance.annotator == ""


class TestBootstrapLabels:
    def test_copies_segments_and_duration(self):
        source = _source_labels()
        boot = bootstrap_labels(source)

        assert boot.audio_duration == 120.0
        assert len(boot.segments) == 1
        assert boot.segments[0].label == "Pre-roll"
        assert boot.episode_ref == source.episode_ref

    def test_keeps_source_annotator_by_default(self):
        source = _source_labels(annotator="claude-haiku-4-5-20251001")
        assert bootstrap_labels(source).provenance.annotator == "claude-haiku-4-5-20251001"

    def test_annotator_override(self):
        boot = bootstrap_labels(_source_labels(), annotator="human")
        assert boot.provenance.annotator == "human"

    def test_result_is_independent_copy(self):
        source = _source_labels()
        boot = bootstrap_labels(source)
        boot.segments[0].label = "EDITED"
        assert source.segments[0].label == "Pre-roll"
