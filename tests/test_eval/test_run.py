"""Tests for eval.run.run_eval — the eval matrix runner."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from podcast_etl.detectors import AdSegment
from podcast_etl.eval_cli import eval_group
from podcast_etl.labels import EpisodeRef, Labels, Provenance
from podcast_etl.models import Episode, StepStatus

from eval.run import run_eval


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


def _gold_labels(slug: str, episode_json: str, segments, annotator="human") -> Labels:
    return Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=600.0,
        segments=segments,
        provenance=Provenance(
            whisper={"model": "base", "language": "en"},
            llm={},
            annotator=annotator,
            created_at=datetime.now().isoformat(),
        ),
    )


def _write_gold(datasets_dir: Path, slug: str, stem: str, labels: Labels) -> None:
    (labels).save(datasets_dir / "gold" / slug / "labels" / f"{stem}.json")


def _two_config_yaml(tmp_path: Path, output_dir: Path, configs) -> Path:
    cfg = {
        "output_dir": str(output_dir),
        "gold": "gold",
        "allowed_annotators": ["human"],
        "configs": configs,
    }
    p = tmp_path / "eval_config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunEval:
    def test_gold_defines_scored_episode_set(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        # Two episodes on disk, but gold only references one.
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        _write_episode(output_dir, "p", "ep2.json")
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg]))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "m"}, "prompt": "default"},
        ])

        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[pred_seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            results = run_eval(config_path, output_dir, datasets_dir, results_dir)

        assert set(results) == {"cfg-a"}
        # Only ep1 (in gold) scored, even though ep2 exists on disk.
        assert results["cfg-a"].episode_count == 1
        assert results["cfg-a"].total_tp == 1

    def test_duplicate_config_names_raise(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", []))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "dup", "whisper": {"model": "base"}, "llm": {"model": "m"}},
            {"name": "dup", "whisper": {"model": "base"}, "llm": {"model": "m"}},
        ])

        with pytest.raises(ValueError, match="Duplicate config name"):
            run_eval(config_path, output_dir, datasets_dir, results_dir)

    def test_shared_whisper_transcribes_once(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg]))

        # Two configs share identical whisper settings.
        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "a"}, "prompt": "default"},
            {"name": "cfg-b", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "b"}, "prompt": "default"},
        ])

        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]) as mock_transcribe, \
             patch("eval.label.classify", return_value=[pred_seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            results = run_eval(config_path, output_dir, datasets_dir, results_dir)

        # One episode, two configs sharing whisper -> transcribed exactly once.
        assert mock_transcribe.call_count == 1
        assert set(results) == {"cfg-a", "cfg-b"}

    def test_results_json_written_per_config(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg]))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "a"}, "prompt": "default"},
        ])

        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[pred_seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            run_eval(config_path, output_dir, datasets_dir, results_dir)

        json_files = list(results_dir.glob("*-cfg-a.json"))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["config"] == "cfg-a"
        assert payload["gold"] == "gold"
        assert payload["aggregate"]["total_tp"] == 1

    def test_unscored_gold_warns_and_records_coverage(self, tmp_path, caplog):
        """Gold episode with no matching prediction emits a warning and records coverage counts."""
        import logging

        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        # Write TWO gold episodes but label_dataset will only produce a prediction
        # for ep1 (ep2's resolve will raise FileNotFoundError → skipped by label_dataset).
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        # ep2: episode JSON exists in gold but NOT on disk → label_dataset skips it
        # so no prediction file is written.
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg]))
        _write_gold(datasets_dir, "p", "ep2", _gold_labels("p", "ep2.json", [seg]))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "m"}, "prompt": "default"},
        ])

        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[pred_seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0), \
             caplog.at_level(logging.WARNING, logger="eval.run"):
            results = run_eval(config_path, output_dir, datasets_dir, results_dir)

        # ep1 scored, ep2 had no prediction → warning emitted
        warning_texts = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ep2.json" in str(w) for w in warning_texts), \
            f"Expected warning mentioning ep2.json; got: {warning_texts}"
        assert any("1/2" in str(w) for w in warning_texts), \
            f"Expected '1/2' coverage in warning; got: {warning_texts}"

        # Results JSON records coverage
        json_files = list(results_dir.glob("*-cfg-a.json"))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["gold_episode_count"] == 2
        assert payload["scored_episode_count"] == 1

    def test_allowed_annotators_filters_gold(self, tmp_path):
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"
        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="model")
        # Gold annotated by a model, not human.
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg], annotator="some-model"))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "a"}, "prompt": "default"},
        ])

        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            results = run_eval(config_path, output_dir, datasets_dir, results_dir)

        # Default allowed_annotators=["human"] filters out the model gold -> nothing scored.
        assert results["cfg-a"].episode_count == 0


# ---------------------------------------------------------------------------
# eval run CLI smoke test
# ---------------------------------------------------------------------------

class TestRunCmd:
    def test_run_cmd_exit_zero_and_writes_results(self, tmp_path):
        """Happy-path: CLI run subcommand forwards args, prints report, writes JSON."""
        output_dir = tmp_path / "output"
        datasets_dir = tmp_path / "datasets"
        results_dir = tmp_path / "results"

        _write_episode(output_dir, "p", "ep1.json")
        _write_audio(output_dir, "p")
        seg = AdSegment(start=10.0, end=40.0, confidence=1.0, detector="human")
        _write_gold(datasets_dir, "p", "ep1", _gold_labels("p", "ep1.json", [seg]))

        config_path = _two_config_yaml(tmp_path, output_dir, [
            {"name": "cfg-a", "whisper": {"model": "base", "language": "en"},
             "llm": {"provider": "anthropic", "model": "m"}, "prompt": "default"},
        ])

        pred_seg = AdSegment(start=11.0, end=39.0, confidence=0.9, detector="transcription")
        runner = CliRunner()
        with patch("eval.label.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "x"}]), \
             patch("eval.label.classify", return_value=[pred_seg]), \
             patch("eval.label.load_prompt", return_value="p"), \
             patch("eval.label.build_llm_client", return_value=None), \
             patch("eval.label._get_audio_duration", return_value=600.0):
            result = runner.invoke(
                eval_group,
                [
                    "run",
                    "--config", str(config_path),
                    "--output-dir", str(output_dir),
                    "--datasets-dir", str(datasets_dir),
                    "--results-dir", str(results_dir),
                ],
            )

        assert result.exit_code == 0, result.output
        # A results JSON was written for cfg-a.
        json_files = list(results_dir.glob("*-cfg-a.json"))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text())
        assert payload["config"] == "cfg-a"
        assert payload["aggregate"]["total_tp"] == 1
        # The printed report contains a header line.
        assert "Config" in result.output
