"""Tests for eval.datasets: dataset loading and path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.datasets import (
    episode_key,
    iter_label_files,
    label_file_path,
    load_dataset,
    resolve_dataset_root,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_labels(podcast_slug: str, episode_json: str) -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=podcast_slug, episode_json=episode_json),
        audio_duration=3600.0,
        segments=[
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="test", label="ad"),
        ],
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "prompt": "default"},
            annotator="claude-haiku-4-5-20251001",
            created_at="2026-01-01T00:00:00",
        ),
    )


def _write_labels(root: Path, podcast_slug: str, stem: str, labels: Labels) -> Path:
    path = root / podcast_slug / "labels" / f"{stem}.json"
    labels.save(path)
    return path


# ---------------------------------------------------------------------------
# episode_key
# ---------------------------------------------------------------------------

class TestEpisodeKey:
    def test_format(self):
        ref = EpisodeRef(podcast_slug="my-podcast", episode_json="2024-01-15-ep-ab12cd34.json")
        assert episode_key(ref) == "my-podcast/2024-01-15-ep-ab12cd34.json"

    def test_different_refs_different_keys(self):
        ref1 = EpisodeRef(podcast_slug="p1", episode_json="ep1.json")
        ref2 = EpisodeRef(podcast_slug="p2", episode_json="ep1.json")
        assert episode_key(ref1) != episode_key(ref2)

    def test_same_ref_same_key(self):
        ref = EpisodeRef(podcast_slug="p", episode_json="ep.json")
        assert episode_key(ref) == episode_key(ref)


# ---------------------------------------------------------------------------
# label_file_path
# ---------------------------------------------------------------------------

class TestLabelFilePath:
    def test_returns_correct_path(self, tmp_path):
        result = label_file_path(tmp_path, "my-podcast", "episode-ab12cd34")
        assert result == tmp_path / "my-podcast" / "labels" / "episode-ab12cd34.json"

    def test_stem_without_extension(self, tmp_path):
        result = label_file_path(tmp_path, "slug", "stem")
        assert result.suffix == ".json"
        assert result.stem == "stem"


# ---------------------------------------------------------------------------
# iter_label_files
# ---------------------------------------------------------------------------

class TestIterLabelFiles:
    def test_yields_label_files(self, tmp_path):
        labels = _make_labels("podcast-a", "ep1.json")
        _write_labels(tmp_path, "podcast-a", "ep1", labels)

        files = list(iter_label_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "ep1.json"

    def test_multiple_podcasts_and_episodes(self, tmp_path):
        for slug in ("podcast-a", "podcast-b"):
            for i in range(2):
                labels = _make_labels(slug, f"ep{i}.json")
                _write_labels(tmp_path, slug, f"ep{i}", labels)

        files = list(iter_label_files(tmp_path))
        assert len(files) == 4

    def test_ignores_files_outside_labels_subdir(self, tmp_path):
        # A JSON directly in the podcast dir should NOT be found
        (tmp_path / "podcast-a").mkdir(parents=True)
        (tmp_path / "podcast-a" / "podcast.json").write_text("{}")

        files = list(iter_label_files(tmp_path))
        assert files == []

    def test_ignores_dotfiles(self, tmp_path):
        # Atomic-write temp files start with "."
        labels_dir = tmp_path / "podcast-a" / "labels"
        labels_dir.mkdir(parents=True)
        (labels_dir / ".ep.json.tmp").write_text("{}")
        real_labels = _make_labels("podcast-a", "ep.json")
        _write_labels(tmp_path, "podcast-a", "ep", real_labels)

        files = list(iter_label_files(tmp_path))
        assert len(files) == 1
        assert files[0].name == "ep.json"

    def test_returns_sorted(self, tmp_path):
        for name in ("c", "a", "b"):
            labels = _make_labels("p", f"{name}.json")
            _write_labels(tmp_path, "p", name, labels)

        files = list(iter_label_files(tmp_path))
        names = [f.stem for f in files]
        assert names == sorted(names)

    def test_empty_root(self, tmp_path):
        files = list(iter_label_files(tmp_path))
        assert files == []


# ---------------------------------------------------------------------------
# load_dataset
# ---------------------------------------------------------------------------

class TestLoadDataset:
    def test_loads_labels_keyed_by_episode_ref(self, tmp_path):
        labels = _make_labels("my-podcast", "ep1.json")
        _write_labels(tmp_path, "my-podcast", "ep1-stem", labels)

        dataset = load_dataset(tmp_path)
        expected_key = "my-podcast/ep1.json"
        assert expected_key in dataset
        assert dataset[expected_key].audio_duration == 3600.0

    def test_key_comes_from_episode_ref_not_filename(self, tmp_path):
        # The episode_ref inside the file says ep1.json, but the file is named
        # differently — the key should still be from episode_ref.
        labels = _make_labels("p", "canonical-name.json")
        _write_labels(tmp_path, "p", "file-name-stem", labels)

        dataset = load_dataset(tmp_path)
        assert "p/canonical-name.json" in dataset
        assert "p/file-name-stem.json" not in dataset

    def test_multiple_episodes_all_loaded(self, tmp_path):
        for i in range(3):
            labels = _make_labels("p", f"ep{i}.json")
            _write_labels(tmp_path, "p", f"ep{i}-stem", labels)

        dataset = load_dataset(tmp_path)
        assert len(dataset) == 3

    def test_missing_root_raises(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError, match="Dataset root not found"):
            load_dataset(nonexistent)

    def test_empty_root_returns_empty_dict(self, tmp_path):
        dataset = load_dataset(tmp_path)
        assert dataset == {}

    def test_segments_loaded_correctly(self, tmp_path):
        labels = _make_labels("p", "ep.json")
        _write_labels(tmp_path, "p", "stem", labels)

        dataset = load_dataset(tmp_path)
        loaded = dataset["p/ep.json"]
        assert len(loaded.segments) == 1
        assert loaded.segments[0].start == 0.0
        assert loaded.segments[0].end == 30.0


# ---------------------------------------------------------------------------
# resolve_dataset_root
# ---------------------------------------------------------------------------

class TestResolveDatasetRoot:
    def test_output_returns_output_dir(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "eval" / "datasets"
        result = resolve_dataset_root("output", output_dir, datasets_dir)
        assert result == output_dir

    def test_existing_directory_returned_as_is(self, tmp_path):
        explicit_dir = tmp_path / "some" / "explicit" / "path"
        explicit_dir.mkdir(parents=True)
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "eval" / "datasets"

        result = resolve_dataset_root(str(explicit_dir), output_dir, datasets_dir)
        assert result == explicit_dir

    def test_named_dataset_resolves_under_datasets_dir(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "eval" / "datasets"

        result = resolve_dataset_root("gold", output_dir, datasets_dir)
        assert result == datasets_dir / "gold"

    def test_nonexistent_path_falls_back_to_named_dataset(self, tmp_path):
        # If the string is not "output" and is not an existing directory,
        # it should be treated as a named dataset even if it looks like a path.
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "eval" / "datasets"

        result = resolve_dataset_root("path/that/does/not/exist", output_dir, datasets_dir)
        assert result == datasets_dir / "path/that/does/not/exist"
