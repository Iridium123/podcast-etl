"""Tests for eval.validate: validate_labels and validate_dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.validate import validate_dataset, validate_labels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_labels(
    segments: list[AdSegment],
    audio_duration: float = 3600.0,
    slug: str = "my-podcast",
    episode_json: str = "ep.json",
) -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=audio_duration,
        segments=segments,
        provenance=Provenance(
            whisper={}, llm={}, annotator="human", created_at="2026-01-01T00:00:00"
        ),
    )


def _seg(start: float, end: float, confidence: float = 0.9) -> AdSegment:
    return AdSegment(start=start, end=end, confidence=confidence, detector="test")


def _write_labels(root: Path, labels: Labels, podcast_slug: str, stem: str) -> Path:
    path = root / podcast_slug / "labels" / f"{stem}.json"
    labels.save(path)
    return path


# ---------------------------------------------------------------------------
# validate_labels — valid cases
# ---------------------------------------------------------------------------

class TestValidateLabelsValid:
    def test_empty_segments_is_valid(self):
        labels = _make_labels(segments=[])
        assert validate_labels(labels) == []

    def test_single_valid_segment(self):
        labels = _make_labels(segments=[_seg(10.0, 60.0)])
        assert validate_labels(labels) == []

    def test_multiple_non_overlapping_segments(self):
        labels = _make_labels(segments=[_seg(0.0, 30.0), _seg(60.0, 90.0)])
        assert validate_labels(labels) == []

    def test_segment_ending_exactly_at_audio_duration(self):
        labels = _make_labels(segments=[_seg(0.0, 3600.0)], audio_duration=3600.0)
        assert validate_labels(labels) == []


# ---------------------------------------------------------------------------
# validate_labels — individual failure types
# ---------------------------------------------------------------------------

class TestValidateLabelsErrors:
    def test_negative_start(self):
        labels = _make_labels(segments=[_seg(-1.0, 10.0)])
        errors = validate_labels(labels)
        assert any("negative start" in e for e in errors), errors

    def test_negative_end(self):
        labels = _make_labels(segments=[_seg(0.0, -5.0)])
        errors = validate_labels(labels)
        assert any("negative end" in e for e in errors), errors

    def test_start_equals_end(self):
        labels = _make_labels(segments=[_seg(10.0, 10.0)])
        errors = validate_labels(labels)
        assert any("start" in e and ">=" in e and "end" in e for e in errors), errors

    def test_start_greater_than_end(self):
        labels = _make_labels(segments=[_seg(50.0, 10.0)])
        errors = validate_labels(labels)
        assert any(">=" in e for e in errors), errors

    def test_end_exceeds_audio_duration(self):
        labels = _make_labels(segments=[_seg(3590.0, 3700.0)], audio_duration=3600.0)
        errors = validate_labels(labels)
        assert any("audio_duration" in e for e in errors), errors

    def test_overlap_between_consecutive_segments(self):
        # seg0 ends at 60, seg1 starts at 50 → overlap
        labels = _make_labels(segments=[_seg(0.0, 60.0), _seg(50.0, 90.0)])
        errors = validate_labels(labels)
        assert any("overlap" in e for e in errors), errors

    def test_error_includes_segment_index(self):
        labels = _make_labels(segments=[_seg(-5.0, 10.0)])
        errors = validate_labels(labels)
        assert any("Segment 0" in e for e in errors), errors

    def test_multiple_errors_returned(self):
        # Two bad segments
        labels = _make_labels(segments=[_seg(-1.0, 10.0), _seg(5.0, 3.0)])
        errors = validate_labels(labels)
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# validate_labels — sorting
# ---------------------------------------------------------------------------

class TestValidateLabelsSorting:
    def test_out_of_order_segments_still_validated_correctly(self):
        """Segments given out of start order must still pass overlap check."""
        # Ordered: [0-30] then [60-90] — no overlap despite being given in reverse.
        labels = _make_labels(segments=[_seg(60.0, 90.0), _seg(0.0, 30.0)])
        assert validate_labels(labels) == []

    def test_out_of_order_overlap_detected(self):
        """Overlap is detected even when segments are provided in reverse order."""
        # Out-of-order input; when sorted: [0-50] and [30-90] overlap
        labels = _make_labels(segments=[_seg(30.0, 90.0), _seg(0.0, 50.0)])
        errors = validate_labels(labels)
        assert any("overlap" in e for e in errors), errors


# ---------------------------------------------------------------------------
# validate_dataset
# ---------------------------------------------------------------------------

class TestValidateDataset:
    def test_empty_dataset_returns_empty_dict(self, tmp_path):
        assert validate_dataset(tmp_path) == {}

    def test_all_valid_returns_entry_per_file_with_empty_list(self, tmp_path):
        labels = _make_labels(segments=[_seg(0.0, 30.0)])
        _write_labels(tmp_path, labels, "my-podcast", "ep1")
        result = validate_dataset(tmp_path)
        assert result == {"my-podcast/labels/ep1.json": []}

    def test_invalid_file_appears_in_result(self, tmp_path):
        bad_labels = _make_labels(segments=[_seg(-1.0, 30.0)])
        _write_labels(tmp_path, bad_labels, "my-podcast", "ep1")
        result = validate_dataset(tmp_path)
        assert len(result) == 1

    def test_keys_are_relative_paths(self, tmp_path):
        bad_labels = _make_labels(segments=[_seg(-1.0, 30.0)])
        _write_labels(tmp_path, bad_labels, "my-podcast", "ep1")
        result = validate_dataset(tmp_path)
        # Key must be relative, not absolute
        key = next(iter(result))
        assert not key.startswith("/")
        assert "my-podcast" in key
        assert "ep1.json" in key

    def test_relative_key_is_rooted_at_dataset_root(self, tmp_path):
        bad_labels = _make_labels(segments=[_seg(-1.0, 30.0)])
        _write_labels(tmp_path, bad_labels, "my-podcast", "ep1")
        result = validate_dataset(tmp_path)
        expected_key = "my-podcast/labels/ep1.json"
        assert expected_key in result

    def test_valid_and_invalid_mixed(self, tmp_path):
        good = _make_labels(segments=[_seg(0.0, 10.0)], slug="p1", episode_json="good.json")
        bad = _make_labels(segments=[_seg(-5.0, 10.0)], slug="p2", episode_json="bad.json")
        _write_labels(tmp_path, good, "p1", "good")
        _write_labels(tmp_path, bad, "p2", "bad")

        result = validate_dataset(tmp_path)
        assert len(result) == 2
        assert result["p1/labels/good.json"] == []
        assert result["p2/labels/bad.json"] != []
        assert any("negative" in e for e in result["p2/labels/bad.json"])

    def test_result_sorted_by_key(self, tmp_path):
        for slug in ("zzz-podcast", "aaa-podcast"):
            bad = _make_labels(
                segments=[_seg(-1.0, 5.0)],
                slug=slug,
                episode_json="ep.json",
            )
            _write_labels(tmp_path, bad, slug, "ep")

        result = validate_dataset(tmp_path)
        keys = list(result.keys())
        assert keys == sorted(keys)

    def test_errors_are_strings(self, tmp_path):
        bad = _make_labels(segments=[_seg(50.0, 10.0)])
        _write_labels(tmp_path, bad, "p", "ep")
        result = validate_dataset(tmp_path)
        errors = next(iter(result.values()))
        assert all(isinstance(e, str) for e in errors)

    def test_multiple_errors_per_file(self, tmp_path):
        bad = _make_labels(
            segments=[_seg(-1.0, 10.0), _seg(5.0, 3.0)],
        )
        _write_labels(tmp_path, bad, "p", "ep")
        result = validate_dataset(tmp_path)
        errors = next(iter(result.values()))
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# validate_labels — zero/negative audio_duration
# ---------------------------------------------------------------------------

class TestValidateLabelsZeroDuration:
    def test_zero_duration_emits_single_duration_error(self):
        """audio_duration=0.0 emits exactly one duration-related error, not one per segment."""
        segs = [_seg(10.0, 40.0), _seg(60.0, 90.0), _seg(120.0, 150.0)]
        labels = _make_labels(segments=segs, audio_duration=0.0)
        errors = validate_labels(labels)
        duration_errors = [e for e in errors if "audio_duration" in e]
        assert len(duration_errors) == 1, (
            f"Expected exactly one audio_duration error; got {len(duration_errors)}: {duration_errors}"
        )

    def test_zero_duration_does_not_emit_per_segment_end_errors(self):
        """With audio_duration=0.0, the per-segment end > audio_duration check is skipped."""
        segs = [_seg(10.0, 40.0), _seg(60.0, 90.0)]
        labels = _make_labels(segments=segs, audio_duration=0.0)
        errors = validate_labels(labels)
        per_seg_end_errors = [e for e in errors if "end" in e and ">" in e and "audio_duration" in e]
        assert per_seg_end_errors == [], (
            f"Expected no per-segment end-bound errors; got: {per_seg_end_errors}"
        )

    def test_zero_duration_still_catches_overlap(self):
        """Even with audio_duration=0.0, overlapping segments are still reported."""
        # [0-50] and [30-80] overlap
        segs = [_seg(0.0, 50.0), _seg(30.0, 80.0)]
        labels = _make_labels(segments=segs, audio_duration=0.0)
        errors = validate_labels(labels)
        assert any("overlap" in e for e in errors), (
            f"Expected overlap error with audio_duration=0; got: {errors}"
        )

    def test_zero_duration_still_catches_negative_start(self):
        """Negative-start check still runs when audio_duration=0.0."""
        segs = [_seg(-5.0, 10.0)]
        labels = _make_labels(segments=segs, audio_duration=0.0)
        errors = validate_labels(labels)
        assert any("negative start" in e for e in errors), errors

    def test_positive_duration_end_bound_still_checked(self):
        """Sanity: when audio_duration > 0, end > audio_duration is still reported."""
        segs = [_seg(3590.0, 3700.0)]
        labels = _make_labels(segments=segs, audio_duration=3600.0)
        errors = validate_labels(labels)
        assert any("audio_duration" in e and "end" in e for e in errors), errors
