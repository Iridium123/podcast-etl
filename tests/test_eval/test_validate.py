"""Tests for annotation validation."""

import pytest

from eval.models import Annotation, EpisodeRef
from eval.validate import validate_annotation, validate_annotations


def _valid_annotation() -> Annotation:
    return Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=3600.0,
        segments=[
            {"start": 0.0, "end": 30.0, "label": "Ad 1", "notes": ""},
            {"start": 100.0, "end": 130.0, "label": "Ad 2", "notes": ""},
        ],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )


class TestValidateAnnotation:
    def test_valid_annotation_passes(self):
        errors = validate_annotation(_valid_annotation())
        assert errors == []

    def test_segment_start_after_end(self):
        ann = _valid_annotation()
        ann.segments[0]["start"] = 40.0
        ann.segments[0]["end"] = 30.0
        errors = validate_annotation(ann)
        assert any("start >= end" in e for e in errors)

    def test_segment_exceeds_duration(self):
        ann = _valid_annotation()
        ann.segments[1]["end"] = 4000.0
        errors = validate_annotation(ann)
        assert any("exceeds audio duration" in e for e in errors)

    def test_overlapping_segments(self):
        ann = _valid_annotation()
        ann.segments[1]["start"] = 20.0  # overlaps with segment 0 (0-30)
        ann.segments[1]["end"] = 50.0
        errors = validate_annotation(ann)
        assert any("overlaps" in e for e in errors)

    def test_negative_start(self):
        ann = _valid_annotation()
        ann.segments[0]["start"] = -1.0
        errors = validate_annotation(ann)
        assert any("negative" in e for e in errors)

    def test_empty_segments_valid(self):
        ann = _valid_annotation()
        ann.segments = []
        errors = validate_annotation(ann)
        assert errors == []

    def test_missing_required_segment_fields(self):
        ann = _valid_annotation()
        ann.segments[0] = {"label": "Ad"}  # missing start and end
        errors = validate_annotation(ann)
        assert any("start" in e for e in errors)


class TestValidateAnnotations:
    def test_validates_all_files(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()

        good = _valid_annotation()
        good.save(ann_dir / "good.json")

        bad = _valid_annotation()
        bad.segments[0]["start"] = 50.0  # start > end (end is 30)
        bad.save(ann_dir / "bad.json")

        results = validate_annotations(ann_dir)
        assert "good.json" in results
        assert results["good.json"] == []
        assert "bad.json" in results
        assert len(results["bad.json"]) > 0

    def test_skips_non_json_files(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()
        (ann_dir / ".gitkeep").touch()
        (ann_dir / "readme.txt").write_text("not json")

        results = validate_annotations(ann_dir)
        assert len(results) == 0
