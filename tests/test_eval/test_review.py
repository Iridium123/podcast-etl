"""Tests for annotation review display."""

from eval.models import Annotation, EpisodeRef
from eval.review import format_review


TRANSCRIPT = [
    {"start": 0.0, "end": 4.2, "text": "Welcome to the show"},
    {"start": 4.2, "end": 8.1, "text": "Before we begin, a word from our sponsor"},
    {"start": 8.1, "end": 42.3, "text": "Squarespace is the all-in-one platform"},
    {"start": 42.3, "end": 43.8, "text": "Visit squarespace.com/myshow for 10% off"},
    {"start": 43.8, "end": 51.0, "text": "Alright, today we're talking about"},
]


def _annotation_with_ad():
    return Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=3600.0,
        segments=[{"start": 8.0, "end": 43.5, "label": "Pre-roll ad", "notes": ""}],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )


class TestFormatReview:
    def test_marks_ad_segments(self):
        output = format_review(_annotation_with_ad(), TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        lines = output.split("\n")

        # Non-ad lines should not have the marker
        assert any("Welcome to the show" in line and "▌" not in line for line in lines)
        # Ad lines should have the marker
        assert any("Squarespace" in line and "▌" in line for line in lines)
        assert any("squarespace.com" in line and "▌" in line for line in lines)
        # Post-ad line should not have marker
        assert any("talking about" in line and "▌" not in line for line in lines)

    def test_shows_audio_path(self):
        output = format_review(_annotation_with_ad(), TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        assert "/output/audio/ep.mp3" in output

    def test_no_ads(self):
        ann = _annotation_with_ad()
        ann.segments = []
        output = format_review(ann, TRANSCRIPT, audio_path="/output/audio/ep.mp3")
        assert "▌" not in output

    def test_empty_transcript(self):
        output = format_review(_annotation_with_ad(), [], audio_path="/output/audio/ep.mp3")
        assert "/output/audio/ep.mp3" in output
