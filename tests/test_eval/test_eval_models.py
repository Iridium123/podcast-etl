"""Tests for eval annotation data model."""

import pytest

from eval.models import Annotation, EpisodeRef


class TestEpisodeRef:
    def test_to_dict(self):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-one-a1b2c3d4.json")
        assert ref.to_dict() == {
            "podcast_slug": "my-podcast",
            "episode_json": "2024-01-15-ep-one-a1b2c3d4.json",
        }

    def test_from_dict(self):
        data = {"podcast_slug": "my-podcast", "episode_json": "2024-01-15-ep-one-a1b2c3d4.json"}
        ref = EpisodeRef.from_dict(data)
        assert ref.podcast_slug == "my-podcast"
        assert ref.episode_json == "2024-01-15-ep-one-a1b2c3d4.json"


class TestAnnotation:
    def _sample_annotation(self) -> Annotation:
        return Annotation(
            episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-a1b2c3d4.json"),
            audio_duration=3600.0,
            segments=[
                {"start": 0.0, "end": 43.5, "label": "Pre-roll ad", "notes": ""},
                {"start": 1820.0, "end": 1892.0, "label": "Mid-roll", "notes": "programmatic"},
            ],
            annotator="human",
            created_at="2026-04-12T10:00:00",
        )

    def test_to_dict_roundtrip(self):
        ann = self._sample_annotation()
        data = ann.to_dict()
        restored = Annotation.from_dict(data)
        assert restored == ann
        assert restored.episode_ref.podcast_slug == "my-podcast"
        assert restored.audio_duration == 3600.0
        assert len(restored.segments) == 2
        assert restored.segments[0]["start"] == 0.0
        assert restored.annotator == "human"

    def test_save_and_load(self, tmp_path):
        ann = self._sample_annotation()
        path = tmp_path / "test-annotation.json"
        ann.save(path)

        loaded = Annotation.load(path)
        assert loaded.episode_ref.episode_json == ann.episode_ref.episode_json
        assert loaded.audio_duration == ann.audio_duration
        assert len(loaded.segments) == 2
        assert loaded.annotator == "human"

    def test_save_creates_parent_dirs(self, tmp_path):
        ann = self._sample_annotation()
        path = tmp_path / "subdir" / "test.json"
        ann.save(path)
        assert path.exists()

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Annotation.load(tmp_path / "missing.json")

    def test_segments_as_ad_segments(self):
        ann = self._sample_annotation()
        ad_segs = ann.segments_as_ad_segments()
        assert len(ad_segs) == 2
        assert ad_segs[0].start == 0.0
        assert ad_segs[0].end == 43.5
        assert ad_segs[0].label == "Pre-roll ad"
        assert ad_segs[0].confidence == 1.0
        assert ad_segs[0].detector == "gold"
