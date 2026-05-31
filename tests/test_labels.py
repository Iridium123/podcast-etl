"""Tests for the Labels artifact: save/load roundtrip and schema."""

import json

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance


def _make_labels():
    return Labels(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-ab12cd34.json"),
        audio_duration=1944.0,
        segments=[
            AdSegment(
                start=0.0, end=27.9, confidence=0.95, detector="transcription",
                label="Pre-roll ad for X", notes="checked by hand",
            ),
        ],
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "prompt": "default"},
            annotator="claude-haiku-4-5-20251001",
            created_at="2026-05-31T12:00:00",
        ),
    )


class TestLabelsRoundtrip:
    def test_to_dict_from_dict_roundtrip(self):
        labels = _make_labels()
        restored = Labels.from_dict(labels.to_dict())
        assert restored == labels

    def test_save_and_load(self, tmp_path):
        labels = _make_labels()
        path = tmp_path / "labels" / "ep.json"
        labels.save(path)

        assert path.exists()
        loaded = Labels.load(path)
        assert loaded == labels

    def test_save_creates_parent_dirs(self, tmp_path):
        labels = _make_labels()
        path = tmp_path / "a" / "b" / "c.json"
        labels.save(path)
        assert path.exists()

    def test_on_disk_shape(self, tmp_path):
        labels = _make_labels()
        path = tmp_path / "ep.json"
        labels.save(path)

        data = json.loads(path.read_text())
        assert data["episode_ref"] == {
            "podcast_slug": "my-podcast", "episode_json": "2024-01-15-ep-ab12cd34.json",
        }
        assert data["audio_duration"] == 1944.0
        assert data["segments"][0]["notes"] == "checked by hand"
        assert data["provenance"]["annotator"] == "claude-haiku-4-5-20251001"
        assert data["provenance"]["llm"]["prompt"] == "default"

    def test_notes_default_empty(self):
        seg = AdSegment(start=0.0, end=1.0, confidence=0.9, detector="transcription")
        assert seg.notes == ""
        assert AdSegment.from_dict(seg.to_dict()).notes == ""
