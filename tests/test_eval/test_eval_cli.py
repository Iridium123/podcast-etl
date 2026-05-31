"""Tests for the podcast-etl eval subcommand group."""

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from podcast_etl.cli import main


def _write_episode(tmp_path: Path, *, with_detect_ads: bool = True, with_llm_provenance: bool = True) -> Path:
    output_dir = tmp_path / "output"
    podcast_dir = output_dir / "p"
    (podcast_dir / "episodes").mkdir(parents=True)
    (podcast_dir / "audio").mkdir()
    (podcast_dir / "audio" / "ep.mp3").write_bytes(b"x")
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "P", "url": "u", "description": None, "image_url": None, "slug": "p",
    }))
    status = {"download": {"completed_at": "2024-01-15T10:00:00", "result": {"path": "audio/ep.mp3"}}}
    if with_detect_ads:
        result = {"segments": [{"start": 0.0, "end": 10.0, "label": "ad"}], "audio_duration": 60.0}
        if with_llm_provenance:
            result["llm"] = {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
        status["detect_ads"] = {"completed_at": "2024-01-15T10:01:00", "result": result}
    (podcast_dir / "episodes" / "ep.json").write_text(json.dumps({
        "title": "Ep", "guid": "g", "published": "2024", "audio_url": "u",
        "duration": "60", "description": "d", "slug": "ep", "status": status,
    }))
    # Make a feeds.yaml so the parent group's load_config doesn't warn (not strictly needed)
    (tmp_path / "feeds.yaml").write_text(yaml.safe_dump({"feeds": []}))
    return output_dir


class TestEvalAnnotateCommand:
    def test_bootstrap_defaults_annotator_from_recorded_model(self, tmp_path):
        output_dir = _write_episode(tmp_path)
        annotations_dir = tmp_path / "annotations"
        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "annotate",
            "p", "ep.json",
            "--output-dir", str(output_dir),
            "--annotations-dir", str(annotations_dir),
        ])
        assert result.exit_code == 0, result.output
        out_file = annotations_dir / "p-ep.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["annotator"] == "claude-haiku-4-5-20251001"
        assert len(data["segments"]) == 1

    def test_explicit_annotator_overrides_default(self, tmp_path):
        output_dir = _write_episode(tmp_path)
        annotations_dir = tmp_path / "annotations"
        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "annotate",
            "p", "ep.json",
            "--output-dir", str(output_dir),
            "--annotations-dir", str(annotations_dir),
            "--annotator", "human",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads((annotations_dir / "p-ep.json").read_text())
        assert data["annotator"] == "human"

    def test_errors_when_episode_missing(self, tmp_path):
        output_dir = _write_episode(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "annotate",
            "p", "missing.json",
            "--output-dir", str(output_dir),
            "--annotations-dir", str(tmp_path / "annotations"),
        ])
        assert result.exit_code != 0
        assert "Episode file not found" in result.output


class TestEvalValidateCommand:
    def test_reports_ok_for_valid_annotation(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()
        (ann_dir / "a.json").write_text(json.dumps({
            "episode_ref": {"podcast_slug": "p", "episode_json": "ep.json"},
            "audio_duration": 60.0,
            "segments": [{"start": 0.0, "end": 10.0, "label": "ad", "notes": ""}],
            "annotator": "human", "created_at": "2026-04-12T10:00:00",
        }))
        (tmp_path / "feeds.yaml").write_text(yaml.safe_dump({"feeds": []}))

        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "validate", str(ann_dir),
        ])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_fails_for_invalid_annotation(self, tmp_path):
        ann_dir = tmp_path / "annotations"
        ann_dir.mkdir()
        (ann_dir / "bad.json").write_text(json.dumps({
            "episode_ref": {"podcast_slug": "p", "episode_json": "ep.json"},
            "audio_duration": 60.0,
            "segments": [{"start": 50.0, "end": 30.0, "label": "x", "notes": ""}],  # start > end
            "annotator": "human", "created_at": "2026-04-12T10:00:00",
        }))
        (tmp_path / "feeds.yaml").write_text(yaml.safe_dump({"feeds": []}))

        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "validate", str(ann_dir),
        ])
        assert result.exit_code != 0
        assert "validation error" in result.output.lower()


class TestEvalAnnotateBlank:
    def test_blank_with_explicit_duration(self, tmp_path):
        output_dir = _write_episode(tmp_path)
        annotations_dir = tmp_path / "annotations"
        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "annotate",
            "p", "ep.json",
            "--blank",
            "--duration", "1234.5",
            "--output-dir", str(output_dir),
            "--annotations-dir", str(annotations_dir),
        ])
        assert result.exit_code == 0, result.output
        data = json.loads((annotations_dir / "p-ep.json").read_text())
        assert data["segments"] == []
        assert data["audio_duration"] == 1234.5

    def test_blank_errors_without_duration_when_audio_missing(self, tmp_path):
        """Audio file does not exist on disk and --duration was not passed."""
        output_dir = _write_episode(tmp_path)
        # _write_episode creates audio/ep.mp3 with fake bytes; remove it to force the failure path
        (output_dir / "p" / "audio" / "ep.mp3").unlink()
        runner = CliRunner()
        result = runner.invoke(main, [
            "-c", str(tmp_path / "feeds.yaml"),
            "eval", "annotate",
            "p", "ep.json",
            "--blank",
            "--output-dir", str(output_dir),
            "--annotations-dir", str(tmp_path / "annotations"),
        ])
        assert result.exit_code != 0
        assert "--duration" in result.output
