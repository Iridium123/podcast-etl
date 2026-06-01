"""Tests for the `podcast-etl eval` CLI surface (src/podcast_etl/eval_cli.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from podcast_etl.detectors import AdSegment
from podcast_etl.eval_cli import eval_group
from podcast_etl.labels import EpisodeRef, Labels, Provenance
from podcast_etl.models import Episode, StepStatus


# ---------------------------------------------------------------------------
# Disk fixtures
# ---------------------------------------------------------------------------

def _make_episode(download_path: str = "audio/episode.mp3") -> Episode:
    return Episode(
        title="Test Episode",
        guid="guid-abc",
        published="Mon, 15 Jan 2024 00:00:00 +0000",
        audio_url="https://example.com/ep.mp3",
        duration="3600",
        description="desc",
        slug="test-episode",
        status={
            "download": StepStatus(
                completed_at="2024-01-15T10:00:00",
                result={"path": download_path, "size_bytes": 1024},
            ),
        },
    )


def _write_episode(output_dir: Path, slug: str, episode_json: str) -> None:
    ep_dir = output_dir / slug / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / episode_json).write_text(json.dumps(_make_episode().to_dict()), encoding="utf-8")


def _write_audio(output_dir: Path, slug: str, relative_path: str = "audio/episode.mp3") -> None:
    audio_path = output_dir / slug / relative_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"ID3" + b"\x00" * 128)


def _make_labels(
    slug: str,
    episode_json: str,
    segments: list[AdSegment],
    *,
    annotator: str = "human",
    duration: float = 600.0,
) -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=duration,
        segments=segments,
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={"provider": "anthropic", "model": "m", "prompt": "default"},
            annotator=annotator,
            created_at=datetime.now().isoformat(),
        ),
    )


def _write_label(dataset_root: Path, slug: str, stem: str, labels: Labels) -> Path:
    path = dataset_root / slug / "labels" / f"{stem}.json"
    labels.save(path)
    return path


# ---------------------------------------------------------------------------
# eval label
# ---------------------------------------------------------------------------

class TestLabelCmd:
    def test_writes_label_files(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        _write_episode(output_dir, "my-podcast", "ep.json")
        _write_audio(output_dir, "my-podcast")

        seg = AdSegment(start=10.0, end=40.0, confidence=0.9, detector="transcription")
        runner = CliRunner()
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 10.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            result = runner.invoke(
                eval_group,
                ["label", "gold", "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
            )

        assert result.exit_code == 0, result.output
        written = datasets_dir / "gold" / "my-podcast" / "labels" / "ep.json"
        assert written.exists()
        labels = Labels.load(written)
        assert len(labels.segments) == 1
        assert "Wrote 1 label file" in result.output

    def test_podcast_filter_limits_scope(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        _write_episode(output_dir, "podcast-a", "a.json")
        _write_audio(output_dir, "podcast-a")
        _write_episode(output_dir, "podcast-b", "b.json")
        _write_audio(output_dir, "podcast-b")

        runner = CliRunner()
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            result = runner.invoke(
                eval_group,
                ["label", "ds", "--podcast", "podcast-a",
                 "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
            )

        assert result.exit_code == 0, result.output
        assert (datasets_dir / "ds" / "podcast-a" / "labels" / "a.json").exists()
        assert not (datasets_dir / "ds" / "podcast-b").exists()

    def test_config_yaml_model_lands_in_provenance(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        _write_episode(output_dir, "my-podcast", "ep.json")
        _write_audio(output_dir, "my-podcast")

        config_path = tmp_path / "cfg.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "whisper": {"model": "base", "language": "en"},
                    "llm": {"provider": "anthropic", "model": "custom-model-9000"},
                    "prompt": "default",
                    "min_confidence": 0.5,
                }
            ),
            encoding="utf-8",
        )

        runner = CliRunner()
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            result = runner.invoke(
                eval_group,
                ["label", "gold", "--config", str(config_path),
                 "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
            )

        assert result.exit_code == 0, result.output
        labels = Labels.load(datasets_dir / "gold" / "my-podcast" / "labels" / "ep.json")
        assert labels.provenance.llm["model"] == "custom-model-9000"
        assert labels.provenance.annotator == "custom-model-9000"

    def test_no_episodes_message(self, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        datasets_dir = tmp_path / "datasets"
        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["label", "gold", "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )
        assert result.exit_code == 0, result.output
        assert "No episodes found" in result.output


# ---------------------------------------------------------------------------
# eval annotate
# ---------------------------------------------------------------------------

class TestAnnotateBlank:
    def test_creates_blank_with_resolved_duration(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        _write_episode(output_dir, "my-podcast", "ep.json")
        _write_audio(output_dir, "my-podcast")

        runner = CliRunner()

        # Patch the mutagen MP3 the command imports lazily.
        class _FakeInfo:
            length = 1234.5

        class _FakeMP3:
            def __init__(self, *_a, **_k):
                self.info = _FakeInfo()

        with patch("mutagen.mp3.MP3", _FakeMP3):
            result = runner.invoke(
                eval_group,
                ["annotate", "my-podcast", "ep", "--blank", "--dataset", "gold",
                 "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
            )

        assert result.exit_code == 0, result.output
        written = datasets_dir / "gold" / "my-podcast" / "labels" / "ep.json"
        assert written.exists()
        labels = Labels.load(written)
        assert labels.provenance.annotator == "human"
        assert labels.audio_duration == 1234.5
        assert labels.segments == []


class TestAnnotateBootstrap:
    def test_copies_source_labels(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        # Source dataset with a model-annotated label.
        seg = AdSegment(start=5.0, end=25.0, confidence=0.8, detector="transcription", label="ad")
        src_labels = _make_labels("my-podcast", "ep.json", [seg], annotator="claude-sonnet-4-6")
        _write_label(datasets_dir / "source-ds", "my-podcast", "ep", src_labels)

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["annotate", "my-podcast", "ep", "--bootstrap-from", "source-ds", "--dataset", "gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )

        assert result.exit_code == 0, result.output
        written = datasets_dir / "gold" / "my-podcast" / "labels" / "ep.json"
        assert written.exists()
        labels = Labels.load(written)
        assert len(labels.segments) == 1
        assert labels.segments[0].start == 5.0
        # Annotator preserved from source until human corrects it.
        assert labels.provenance.annotator == "claude-sonnet-4-6"
        assert "human" in result.output  # reminder printed

    def test_blank_and_bootstrap_mutually_exclusive(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["annotate", "p", "ep", "--blank", "--bootstrap-from", "x"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# eval validate
# ---------------------------------------------------------------------------

class TestValidateCmd:
    def test_valid_dataset_exit_zero(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        good = AdSegment(start=10.0, end=40.0, confidence=0.9, detector="t")
        _write_label(datasets_dir / "gold", "p", "ep", _make_labels("p", "ep.json", [good]))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["validate", "gold", "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )
        assert result.exit_code == 0, result.output
        assert "all valid" in result.output

    def test_invalid_dataset_exit_nonzero_and_names_file(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        # start >= end -> invalid
        bad = AdSegment(start=40.0, end=10.0, confidence=0.9, detector="t")
        _write_label(datasets_dir / "gold", "p", "broken", _make_labels("p", "broken.json", [bad]))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["validate", "gold", "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )
        assert result.exit_code == 1
        assert "broken.json" in result.output


# ---------------------------------------------------------------------------
# eval score
# ---------------------------------------------------------------------------

class TestScoreCmd:
    def test_produces_results_json_and_report(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        gold_seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_label(datasets_dir / "gold", "p", "ep", _make_labels("p", "ep.json", [gold_seg], annotator="human"))
        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        _write_label(datasets_dir / "preds", "p", "ep", _make_labels("p", "ep.json", [pred_seg], annotator="model"))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["score", "--predictions", "preds", "--gold", "gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
             "--results-dir", str(results_dir)],
        )

        assert result.exit_code == 0, result.output
        # A results JSON was written.
        json_files = list(results_dir.glob("*.json"))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["config"] == "preds"
        assert payload["gold"] == "gold"
        assert payload["aggregate"]["total_tp"] == 1
        # Report header present.
        assert "Config" in result.output

    def test_gold_annotator_filter_excludes_non_allowed(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        gold_seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="model")
        # Gold annotated by a model, NOT human -> default filter excludes it.
        _write_label(datasets_dir / "gold", "p", "ep", _make_labels("p", "ep.json", [gold_seg], annotator="some-model"))
        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        _write_label(datasets_dir / "preds", "p", "ep", _make_labels("p", "ep.json", [pred_seg], annotator="model"))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["score", "--predictions", "preds", "--gold", "gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
             "--results-dir", str(results_dir)],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(next(results_dir.glob("*.json")).read_text())
        # No gold episodes survive the filter -> nothing scored.
        assert payload["aggregate"]["episode_count"] == 0
        assert "Skipped 1" in result.output

    def test_only_episodes_in_both_scored(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="x")
        # Gold has two episodes; predictions only has one.
        _write_label(datasets_dir / "gold", "p", "ep1", _make_labels("p", "ep1.json", [seg]))
        _write_label(datasets_dir / "gold", "p", "ep2", _make_labels("p", "ep2.json", [seg]))
        _write_label(datasets_dir / "preds", "p", "ep1", _make_labels("p", "ep1.json", [seg], annotator="model"))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["score", "--predictions", "preds", "--gold", "gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
             "--results-dir", str(results_dir)],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(next(results_dir.glob("*.json")).read_text())
        # Only ep1 is in both -> exactly one episode scored.
        assert payload["aggregate"]["episode_count"] == 1

    def test_missing_gold_exits_nonzero_with_message(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["score", "--predictions", "preds", "--gold", "no-such-gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
             "--results-dir", str(results_dir)],
        )

        assert result.exit_code != 0
        assert "gold dataset not found" in (result.output + (result.stderr or ""))
        assert "no-such-gold" in (result.output + (result.stderr or ""))

    def test_missing_predictions_exits_nonzero_with_message(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_label(datasets_dir / "gold", "p", "ep", _make_labels("p", "ep.json", [seg], annotator="human"))

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["score", "--predictions", "no-such-preds", "--gold", "gold",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir),
             "--results-dir", str(results_dir)],
        )

        assert result.exit_code != 0
        assert "predictions dataset not found" in (result.output + (result.stderr or ""))
        assert "no-such-preds" in (result.output + (result.stderr or ""))


# ---------------------------------------------------------------------------
# eval validate — error cases
# ---------------------------------------------------------------------------

class TestValidateCmdErrors:
    def test_missing_dataset_exits_nonzero_with_message(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["validate", "no-such-dataset",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )

        assert result.exit_code != 0
        assert "dataset not found" in (result.output + (result.stderr or ""))
        assert "no-such-dataset" in (result.output + (result.stderr or ""))


# ---------------------------------------------------------------------------
# eval label — error cases
# ---------------------------------------------------------------------------

class TestLabelCmdErrors:
    def test_missing_podcast_slug_exits_nonzero_with_message(self, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        datasets_dir = tmp_path / "datasets"

        runner = CliRunner()
        result = runner.invoke(
            eval_group,
            ["label", "gold", "--podcast", "no-such-podcast",
             "--output-dir", str(output_dir), "--datasets-dir", str(datasets_dir)],
        )

        assert result.exit_code != 0
        assert "no-such-podcast" in (result.output + (result.stderr or ""))
