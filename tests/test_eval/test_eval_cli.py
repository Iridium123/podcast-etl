"""Tests for the `podcast-etl eval` subcommand group."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from podcast_etl.cli import main
from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance


def _feeds(tmp_path: Path) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml.safe_dump({"feeds": []}))
    return path


def _episode(output_dir: Path, *, with_labels: bool = False):
    podcast_dir = output_dir / "p"
    (podcast_dir / "episodes").mkdir(parents=True)
    (podcast_dir / "audio").mkdir()
    (podcast_dir / "audio" / "ep.mp3").write_bytes(b"x")
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "P", "url": "u", "description": None, "image_url": None, "slug": "p",
    }))
    (podcast_dir / "episodes" / "ep.json").write_text(json.dumps({
        "title": "Ep", "guid": "g", "published": "2024-01-15", "audio_url": "u",
        "duration": "60", "description": "d", "slug": "ep",
        "status": {"download": {"completed_at": "t", "result": {"path": "audio/ep.mp3"}}},
    }))
    if with_labels:
        Labels(
            episode_ref=EpisodeRef(podcast_slug="p", episode_json="ep.json"),
            audio_duration=60.0,
            segments=[AdSegment(0.0, 10.0, 0.9, "transcription", "Pre-roll")],
            provenance=Provenance(
                whisper={"model": "base", "language": "en"},
                llm={"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "prompt": "default"},
                annotator="claude-haiku-4-5-20251001", created_at="t",
            ),
        ).save(podcast_dir / "labels" / "ep.json")
    return podcast_dir


def _dataset_with_labels(root: Path, annotator="human", segments=None):
    Labels(
        episode_ref=EpisodeRef(podcast_slug="p", episode_json="ep.json"),
        audio_duration=60.0,
        segments=segments or [AdSegment(0.0, 10.0, 1.0, "gold", "ad")],
        provenance=Provenance(whisper={}, llm={}, annotator=annotator, created_at="t"),
    ).save(root / "p" / "labels" / "ep.json")
    return root


class TestValidateCommand:
    def test_ok_on_valid_dataset(self, tmp_path):
        gold = _dataset_with_labels(tmp_path / "gold")
        result = CliRunner().invoke(main, [
            "-c", str(_feeds(tmp_path)), "eval", "validate", str(gold),
        ])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_fails_on_invalid_dataset(self, tmp_path):
        gold = _dataset_with_labels(tmp_path / "gold", segments=[AdSegment(30.0, 10.0, 1.0, "gold", "x")])
        result = CliRunner().invoke(main, [
            "-c", str(_feeds(tmp_path)), "eval", "validate", str(gold),
        ])
        assert result.exit_code != 0
        assert "start >= end" in result.output


class TestScoreCommand:
    def test_scores_and_writes_results(self, tmp_path):
        gold = _dataset_with_labels(tmp_path / "gold", annotator="human")
        preds = _dataset_with_labels(
            tmp_path / "preds", annotator="claude-haiku-4-5-20251001",
            segments=[AdSegment(0.0, 10.0, 0.9, "transcription", "ad")],
        )
        results_dir = tmp_path / "results"
        result = CliRunner().invoke(main, [
            "-c", str(_feeds(tmp_path)), "eval", "score",
            "--predictions", str(preds), "--gold", str(gold),
            "--results-dir", str(results_dir),
        ])
        assert result.exit_code == 0, result.output
        assert "Prec" in result.output  # report header
        assert list(results_dir.glob("*.json"))  # a results file landed


class TestAnnotateCommand:
    def test_bootstrap_from_production(self, tmp_path):
        output_dir = tmp_path / "output"
        _episode(output_dir, with_labels=True)
        datasets_dir = tmp_path / "datasets"
        result = CliRunner().invoke(main, [
            "-c", str(_feeds(tmp_path)), "eval", "annotate", "p", "ep.json",
            "--dataset", "gold", "--output-dir", str(output_dir),
            "--datasets-dir", str(datasets_dir),
        ])
        assert result.exit_code == 0, result.output
        out = datasets_dir / "gold" / "p" / "labels" / "ep.json"
        assert out.exists()
        labels = Labels.load(out)
        assert len(labels.segments) == 1
        assert labels.segments[0].label == "Pre-roll"

    def test_blank_annotation(self, tmp_path):
        output_dir = tmp_path / "output"
        _episode(output_dir, with_labels=False)
        datasets_dir = tmp_path / "datasets"
        with patch("podcast_etl.eval_cli._audio_duration", return_value=60.0):
            result = CliRunner().invoke(main, [
                "-c", str(_feeds(tmp_path)), "eval", "annotate", "p", "ep.json",
                "--blank", "--dataset", "gold", "--output-dir", str(output_dir),
                "--datasets-dir", str(datasets_dir),
            ])
        assert result.exit_code == 0, result.output
        labels = Labels.load(datasets_dir / "gold" / "p" / "labels" / "ep.json")
        assert labels.segments == []
        assert labels.audio_duration == 60.0


class TestLabelCommand:
    def test_writes_labels_for_episode(self, tmp_path):
        output_dir = tmp_path / "output"
        _episode(output_dir, with_labels=False)
        datasets_dir = tmp_path / "datasets"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "default.txt").write_text("classify ads")

        predicted = [AdSegment(0.0, 10.0, 0.9, "transcription", "Pre-roll")]
        with patch("eval.run.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "ad"}]), \
             patch("eval.run.classify_to_segments", return_value=predicted), \
             patch("eval.run._audio_duration", return_value=60.0), \
             patch("eval.run.build_llm_client", return_value=None):
            result = CliRunner().invoke(main, [
                "-c", str(_feeds(tmp_path)), "eval", "label", "preds",
                "--podcast", "p", "--episode", "ep.json",
                "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
                "--prompts-dir", str(prompts_dir), "--model", "claude-haiku-4-5-20251001",
            ])
        assert result.exit_code == 0, result.output
        labels = Labels.load(datasets_dir / "preds" / "p" / "labels" / "ep.json")
        assert labels.segments[0].label == "Pre-roll"


class TestRunCommand:
    def test_missing_config_errors(self, tmp_path):
        result = CliRunner().invoke(main, [
            "-c", str(_feeds(tmp_path)), "eval", "run",
            "--config", str(tmp_path / "nope.yaml"),
        ])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
