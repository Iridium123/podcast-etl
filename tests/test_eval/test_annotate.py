"""Tests for eval.annotate: create_blank and bootstrap_from_dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.annotate import bootstrap_from_dataset, create_blank
from eval.datasets import episode_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ref(slug: str = "my-podcast", episode_json: str = "ep.json") -> EpisodeRef:
    return EpisodeRef(podcast_slug=slug, episode_json=episode_json)


def _make_labels(ref: EpisodeRef, annotator: str = "claude-haiku") -> Labels:
    return Labels(
        episode_ref=ref,
        audio_duration=3600.0,
        segments=[
            AdSegment(start=10.0, end=60.0, confidence=0.95, detector="llm", label="promo"),
        ],
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": "claude-haiku", "prompt": "default"},
            annotator=annotator,
            created_at="2026-01-01T00:00:00",
        ),
    )


def _write_labels_to_dataset(root: Path, labels: Labels, stem: str) -> Path:
    """Write a Labels file into a dataset rooted at *root*."""
    ref = labels.episode_ref
    path = root / ref.podcast_slug / "labels" / f"{stem}.json"
    labels.save(path)
    return path


# ---------------------------------------------------------------------------
# create_blank
# ---------------------------------------------------------------------------

class TestCreateBlank:
    def test_returns_labels(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0)
        assert isinstance(result, Labels)

    def test_empty_segments(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0)
        assert result.segments == []

    def test_episode_ref_preserved(self):
        ref = _make_ref(slug="news-pod", episode_json="ep42.json")
        result = create_blank(ref, audio_duration=100.0)
        assert result.episode_ref == ref

    def test_audio_duration_preserved(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=7200.5)
        assert result.audio_duration == 7200.5

    def test_default_annotator_is_human(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0)
        assert result.provenance.annotator == "human"

    def test_custom_annotator(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0, annotator="reviewer-1")
        assert result.provenance.annotator == "reviewer-1"

    def test_provenance_whisper_and_llm_are_empty(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0)
        assert result.provenance.whisper == {}
        assert result.provenance.llm == {}

    def test_created_at_is_set(self):
        ref = _make_ref()
        result = create_blank(ref, audio_duration=1800.0)
        # Must be a non-empty ISO 8601-ish string
        assert result.provenance.created_at
        assert "T" in result.provenance.created_at  # basic ISO 8601 shape

    def test_does_not_persist(self, tmp_path):
        """create_blank should not write any file."""
        ref = _make_ref()
        create_blank(ref, audio_duration=100.0)
        assert list(tmp_path.rglob("*.json")) == []


# ---------------------------------------------------------------------------
# bootstrap_from_dataset
# ---------------------------------------------------------------------------

class TestBootstrapFromDataset:
    def test_returns_labels_from_source(self, tmp_path):
        ref = _make_ref()
        source_labels = _make_labels(ref)
        _write_labels_to_dataset(tmp_path, source_labels, "ep-stem")

        result = bootstrap_from_dataset(ref, source_root=tmp_path)

        assert isinstance(result, Labels)
        assert result.episode_ref == ref

    def test_segments_copied(self, tmp_path):
        ref = _make_ref()
        source_labels = _make_labels(ref)
        _write_labels_to_dataset(tmp_path, source_labels, "ep-stem")

        result = bootstrap_from_dataset(ref, source_root=tmp_path)

        assert len(result.segments) == 1
        assert result.segments[0].start == 10.0
        assert result.segments[0].end == 60.0
        assert result.segments[0].label == "promo"

    def test_provenance_copied_including_annotator(self, tmp_path):
        ref = _make_ref()
        source_labels = _make_labels(ref, annotator="claude-haiku")
        _write_labels_to_dataset(tmp_path, source_labels, "ep-stem")

        result = bootstrap_from_dataset(ref, source_root=tmp_path)

        # Source annotator preserved — human must explicitly set it to "human"
        assert result.provenance.annotator == "claude-haiku"
        assert result.provenance.llm["model"] == "claude-haiku"
        assert result.provenance.created_at == "2026-01-01T00:00:00"

    def test_audio_duration_copied(self, tmp_path):
        ref = _make_ref()
        source_labels = _make_labels(ref)
        _write_labels_to_dataset(tmp_path, source_labels, "ep-stem")

        result = bootstrap_from_dataset(ref, source_root=tmp_path)

        assert result.audio_duration == 3600.0

    def test_does_not_persist(self, tmp_path):
        """bootstrap_from_dataset should not write any new file."""
        ref = _make_ref()
        source_labels = _make_labels(ref)
        existing = _write_labels_to_dataset(tmp_path, source_labels, "ep-stem")
        files_before = set(tmp_path.rglob("*.json"))

        bootstrap_from_dataset(ref, source_root=tmp_path)

        assert set(tmp_path.rglob("*.json")) == files_before

    def test_missing_ref_raises_value_error(self, tmp_path):
        ref = _make_ref(episode_json="missing.json")
        # Write a different episode so the dataset is non-empty
        other_ref = _make_ref(episode_json="other.json")
        _write_labels_to_dataset(tmp_path, _make_labels(other_ref), "other-stem")

        with pytest.raises(ValueError, match="my-podcast/missing.json"):
            bootstrap_from_dataset(ref, source_root=tmp_path)

    def test_error_message_includes_source_root(self, tmp_path):
        ref = _make_ref(episode_json="ghost.json")
        # No files in the dataset at all
        with pytest.raises(ValueError, match=str(tmp_path)):
            bootstrap_from_dataset(ref, source_root=tmp_path)

    def test_lookup_by_episode_ref_not_filename(self, tmp_path):
        """File may have any stem; lookup must use episode_ref embedded in file."""
        ref = _make_ref(episode_json="canonical.json")
        source_labels = _make_labels(ref)
        # Save under a different stem
        _write_labels_to_dataset(tmp_path, source_labels, "different-filename-stem")

        result = bootstrap_from_dataset(ref, source_root=tmp_path)
        assert result.episode_ref.episode_json == "canonical.json"
