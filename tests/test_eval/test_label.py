"""Tests for eval.label: classify a transcript into a Labels file."""

from __future__ import annotations

from unittest.mock import patch

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef

from eval.label import classify_to_segments, make_labels


class TestClassifyToSegments:
    def test_calls_production_classify_and_resolves_overlaps(self):
        transcript = [{"start": 0.0, "end": 10.0, "text": "ad"}]
        # production classify returns overlapping segments; resolve_overlaps cleans them
        raw = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="a"),
            AdSegment(start=20.0, end=50.0, confidence=0.9, detector="transcription", label="b"),
        ]
        with patch("eval.label.classify", return_value=raw) as mock_classify:
            segments = classify_to_segments(transcript, {"model": "m"}, "PROMPT", client="C")

        mock_classify.assert_called_once_with(transcript, "PROMPT", {"model": "m"}, client="C")
        # overlap resolved: second segment's start snapped to 30.0, none dropped
        assert [(s.start, s.end) for s in segments] == [(0.0, 30.0), (30.0, 50.0)]


class TestMakeLabels:
    def test_builds_labels_with_provenance(self):
        ref = EpisodeRef(podcast_slug="pod", episode_json="ep.json")
        segments = [AdSegment(start=0.0, end=10.0, confidence=0.9, detector="transcription", label="a")]

        labels = make_labels(
            ref=ref,
            audio_duration=100.0,
            segments=segments,
            whisper={"model": "base", "language": "en", "api_key": "secret"},
            llm_config={"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
            prompt_name="default",
            created_at="2026-05-31T12:00:00",
        )

        assert labels.episode_ref == ref
        assert labels.audio_duration == 100.0
        assert labels.segments == segments
        # whisper is normalized (api_key dropped)
        assert labels.provenance.whisper == {"model": "base", "language": "en"}
        assert labels.provenance.llm == {
            "provider": "anthropic", "model": "claude-haiku-4-5-20251001", "prompt": "default",
        }
        # annotator defaults to the llm model
        assert labels.provenance.annotator == "claude-haiku-4-5-20251001"
        assert labels.provenance.created_at == "2026-05-31T12:00:00"

    def test_annotator_override(self):
        labels = make_labels(
            ref=EpisodeRef(podcast_slug="p", episode_json="e.json"),
            audio_duration=1.0,
            segments=[],
            whisper={},
            llm_config={"model": "m"},
            prompt_name="default",
            annotator="human",
            created_at="t",
        )
        assert labels.provenance.annotator == "human"
