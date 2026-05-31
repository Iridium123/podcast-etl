"""Tests for eval.review: transcript display with ad-segment highlights."""

from __future__ import annotations

import json

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.review import format_review, review_labels_file


def _labels(segments, slug="my-podcast", episode_json="ep.json"):
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=100.0,
        segments=segments,
        provenance=Provenance(
            whisper={}, llm={}, annotator="human", created_at="2026-05-31T00:00:00",
        ),
    )


class TestFormatReview:
    def test_highlights_lines_inside_ad_segments(self):
        labels = _labels([AdSegment(start=0.0, end=15.0, confidence=1.0, detector="gold", label="Pre-roll")])
        transcript = [
            {"start": 0.0, "end": 10.0, "text": "Brought to you by Acme"},
            {"start": 20.0, "end": 30.0, "text": "Welcome to the show"},
        ]

        out = format_review(labels, transcript, "/audio.mp3")

        ad_line = next(line for line in out.splitlines() if "Acme" in line)
        normal_line = next(line for line in out.splitlines() if "Welcome" in line)
        assert "▌" in ad_line  # left half block marks ad lines
        assert "AD" in ad_line
        assert "▌" not in normal_line

    def test_handles_empty_transcript(self):
        out = format_review(_labels([]), [], "/audio.mp3")
        assert "/audio.mp3" in out


class TestReviewLabelsFile:
    def _setup_episode(self, tmp_path, transcript=None):
        podcast_dir = tmp_path / "my-podcast"
        (podcast_dir / "episodes").mkdir(parents=True)
        (podcast_dir / "audio").mkdir(parents=True)
        (podcast_dir / "audio" / "ep.mp3").write_bytes(b"x")
        (podcast_dir / "episodes" / "ep.json").write_text(json.dumps({
            "title": "Ep", "guid": "g", "published": "2024-01-15",
            "audio_url": "u", "duration": "10", "description": "d", "slug": "ep",
            "status": {"download": {"completed_at": "t", "result": {"path": "audio/ep.mp3"}}},
        }))
        if transcript is not None:
            (podcast_dir / "transcripts").mkdir(parents=True)
            (podcast_dir / "transcripts" / "ep.json").write_text(json.dumps(transcript))
        return podcast_dir

    def test_loads_labels_and_transcript(self, tmp_path):
        self._setup_episode(tmp_path, transcript=[{"start": 0.0, "end": 5.0, "text": "ad copy"}])
        labels = _labels(
            [AdSegment(start=0.0, end=5.0, confidence=1.0, detector="gold", label="Pre-roll")],
            episode_json="ep.json",
        )
        labels_path = tmp_path / "my-podcast" / "labels" / "ep.json"
        labels.save(labels_path)

        out = review_labels_file(labels_path, tmp_path)

        assert "ad copy" in out
        assert "AD" in out
