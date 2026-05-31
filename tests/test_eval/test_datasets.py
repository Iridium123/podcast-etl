"""Tests for eval.datasets: loading directories of Labels files as datasets."""

from __future__ import annotations

from pathlib import Path

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.datasets import load_dataset, ref_key, resolve_dataset_path


def _labels(slug: str, episode_json: str, annotator: str = "human") -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=100.0,
        segments=[AdSegment(start=0.0, end=10.0, confidence=1.0, detector="gold", label="ad")],
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": "m", "prompt": "default"},
            annotator=annotator,
            created_at="2026-05-31T00:00:00",
        ),
    )


def _write(root: Path, labels: Labels, stem: str) -> Path:
    path = root / labels.episode_ref.podcast_slug / "labels" / f"{stem}.json"
    labels.save(path)
    return path


class TestRefKey:
    def test_combines_slug_and_episode_json(self):
        ref = EpisodeRef(podcast_slug="pod", episode_json="2026-01-01-x-ab12.json")
        assert ref_key(ref) == "pod/2026-01-01-x-ab12.json"


class TestLoadDataset:
    def test_loads_label_files_keyed_by_ref(self, tmp_path):
        _write(tmp_path, _labels("pod-a", "ep1.json"), "audio-1")
        _write(tmp_path, _labels("pod-b", "ep2.json"), "audio-2")

        dataset = load_dataset(tmp_path)

        assert set(dataset) == {"pod-a/ep1.json", "pod-b/ep2.json"}
        assert isinstance(dataset["pod-a/ep1.json"], Labels)
        assert dataset["pod-a/ep1.json"].segments[0].end == 10.0

    def test_keys_by_episode_ref_not_filename(self, tmp_path):
        # The label *filename* is the audio stem; the dataset key comes from the
        # episode_ref inside the file, so production output is interchangeable.
        _write(tmp_path, _labels("pod", "2026-03-19-boaz-edfb31cd.json"), "2026-03-19-boaz")
        dataset = load_dataset(tmp_path)
        assert list(dataset) == ["pod/2026-03-19-boaz-edfb31cd.json"]

    def test_ignores_non_label_directories_and_files(self, tmp_path):
        _write(tmp_path, _labels("pod", "ep.json"), "audio")
        # transcripts/episodes dirs and stray files must be skipped
        (tmp_path / "pod" / "transcripts").mkdir(parents=True)
        (tmp_path / "pod" / "transcripts" / "audio.json").write_text("{}")
        (tmp_path / "pod" / "labels" / "notes.txt").write_text("ignore me")

        dataset = load_dataset(tmp_path)
        assert list(dataset) == ["pod/ep.json"]

    def test_empty_dataset_returns_empty_dict(self, tmp_path):
        assert load_dataset(tmp_path) == {}

    def test_duplicate_ref_warns(self, tmp_path, caplog):
        # Two files, same episode_ref under different filenames -> warn, last wins
        _write(tmp_path, _labels("pod", "ep.json"), "audio-a")
        _write(tmp_path, _labels("pod", "ep.json"), "audio-b")
        with caplog.at_level("WARNING"):
            dataset = load_dataset(tmp_path)
        assert list(dataset) == ["pod/ep.json"]
        assert any("Duplicate episode_ref" in r.message for r in caplog.records)

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_dataset(tmp_path / "does-not-exist")


class TestResolveDatasetPath:
    def test_existing_path_used_directly(self, tmp_path):
        (tmp_path / "pod" / "labels").mkdir(parents=True)
        assert resolve_dataset_path(str(tmp_path), datasets_dir=tmp_path / "ds") == tmp_path

    def test_bare_name_resolves_under_datasets_dir(self, tmp_path):
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "gold").mkdir(parents=True)
        assert resolve_dataset_path("gold", datasets_dir=datasets_dir) == datasets_dir / "gold"
