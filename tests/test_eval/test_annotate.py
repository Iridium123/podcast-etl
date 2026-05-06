"""Tests for annotation bootstrap and creation."""

import pytest

from podcast_etl.models import Episode, StepStatus

from eval.annotate import bootstrap_from_episode, create_blank
from eval.models import Annotation, EpisodeRef


def _episode_with_ads():
    """Create an episode with detect_ads status."""
    return Episode(
        title="Episode One",
        guid="guid-1",
        published="Mon, 15 Jan 2024 10:00:00 GMT",
        audio_url="https://example.com/ep.mp3",
        duration="3600",
        description="An episode",
        slug="episode-one",
        raw_title="Episode One",
        status={
            "download": StepStatus(
                completed_at="2024-01-15T10:00:00",
                result={"path": "audio/episode.mp3", "size_bytes": 1024},
            ),
            "detect_ads": StepStatus(
                completed_at="2024-01-15T10:05:00",
                result={
                    "segments": [
                        {"start": 0.0, "end": 30.0, "confidence": 0.9,
                         "detector": "transcription", "label": "Pre-roll ad"},
                        {"start": 1800.0, "end": 1860.0, "confidence": 0.85,
                         "detector": "transcription", "label": "Mid-roll ad"},
                    ],
                    "audio_duration": 3600.0,
                    "detectors_used": ["transcription"],
                },
            ),
        },
    )


class TestBootstrapFromEpisode:
    def test_creates_annotation_from_detect_ads(self):
        ep = _episode_with_ads()
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = bootstrap_from_episode(ep, ref, annotator="claude-sonnet-4-20250514")

        assert ann.audio_duration == 3600.0
        assert len(ann.segments) == 2
        assert ann.segments[0]["start"] == 0.0
        assert ann.segments[0]["end"] == 30.0
        assert ann.segments[0]["label"] == "Pre-roll ad"
        assert ann.annotator == "claude-sonnet-4-20250514"
        assert ann.episode_ref.podcast_slug == "my-podcast"

    def test_raises_when_no_detect_ads(self):
        ep = Episode(
            title="Ep", guid="g", published=None, audio_url=None,
            duration=None, description=None, slug="ep",
            status={},
        )
        ref = EpisodeRef(podcast_slug="p", episode_json="ep.json")
        with pytest.raises(ValueError, match="no detect_ads"):
            bootstrap_from_episode(ep, ref, annotator="test")

    def test_segments_have_empty_notes(self):
        ep = _episode_with_ads()
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = bootstrap_from_episode(ep, ref, annotator="model")

        for seg in ann.segments:
            assert "notes" in seg
            assert seg["notes"] == ""


class TestCreateBlank:
    def test_creates_blank_annotation(self):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = create_blank(ref, audio_duration=1800.0)

        assert ann.episode_ref == ref
        assert ann.audio_duration == 1800.0
        assert ann.segments == []
        assert ann.annotator == ""

    def test_blank_save_and_load(self, tmp_path):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json")
        ann = create_blank(ref, audio_duration=1800.0)
        path = tmp_path / "blank.json"
        ann.save(path)

        loaded = Annotation.load(path)
        assert loaded.segments == []
        assert loaded.audio_duration == 1800.0
