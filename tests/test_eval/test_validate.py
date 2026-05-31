"""Tests for eval.validate: structural checks on Labels files."""

from __future__ import annotations

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.validate import validate_dataset, validate_labels


def _labels(segments, audio_duration=100.0):
    return Labels(
        episode_ref=EpisodeRef(podcast_slug="pod", episode_json="ep.json"),
        audio_duration=audio_duration,
        segments=segments,
        provenance=Provenance(
            whisper={}, llm={}, annotator="human", created_at="2026-05-31T00:00:00",
        ),
    )


def _seg(start, end):
    return AdSegment(start=start, end=end, confidence=1.0, detector="gold", label="ad")


class TestValidateLabels:
    def test_valid_labels_have_no_errors(self):
        labels = _labels([_seg(0, 10), _seg(20, 30)])
        assert validate_labels(labels) == []

    def test_start_after_end_flagged(self):
        errors = validate_labels(_labels([_seg(30, 10)]))
        assert any("start >= end" in e for e in errors)

    def test_negative_timestamp_flagged(self):
        errors = validate_labels(_labels([_seg(-5, 10)]))
        assert any("negative" in e for e in errors)

    def test_end_exceeds_audio_duration_flagged(self):
        errors = validate_labels(_labels([_seg(0, 200)], audio_duration=100.0))
        assert any("exceeds audio duration" in e for e in errors)

    def test_overlapping_segments_flagged(self):
        errors = validate_labels(_labels([_seg(0, 30), _seg(20, 40)]))
        assert any("overlaps" in e for e in errors)

    def test_adjacent_segments_ok(self):
        # end == next.start is contiguous, not overlapping
        assert validate_labels(_labels([_seg(0, 30), _seg(30, 40)])) == []


class TestValidateDataset:
    def test_validates_each_label_file(self, tmp_path):
        good = _labels([_seg(0, 10)])
        bad = _labels([_seg(30, 10)])
        good.save(tmp_path / "pod" / "labels" / "good.json")
        bad.save(tmp_path / "pod" / "labels" / "bad.json")

        results = validate_dataset(tmp_path)

        assert results["good.json"] == []
        assert any("start >= end" in e for e in results["bad.json"])

    def test_missing_dataset_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_dataset(tmp_path / "nope")
