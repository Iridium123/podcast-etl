"""Tests for the eval runner."""

import json
from unittest.mock import patch

import pytest

from podcast_etl.detectors import AdSegment

from eval.models import Annotation, EpisodeRef
from eval.run import (
    EvalConfig,
    RunConfig,
    group_configs_by_whisper,
    load_prompt,
    load_run_config,
    run_eval,
)


def _setup_annotation(tmp_path):
    """Create a minimal annotation file and matching episode on disk."""
    # Annotation
    ann_dir = tmp_path / "annotations"
    ann_dir.mkdir()
    ann = Annotation(
        episode_ref=EpisodeRef(podcast_slug="my-podcast", episode_json="ep.json"),
        audio_duration=120.0,
        segments=[{"start": 0.0, "end": 30.0, "label": "Pre-roll", "notes": ""}],
        annotator="human",
        created_at="2026-04-12T10:00:00",
    )
    ann.save(ann_dir / "ep-ann.json")

    # Episode on disk
    output_dir = tmp_path / "output"
    podcast_dir = output_dir / "my-podcast"
    podcast_dir.mkdir(parents=True)
    (podcast_dir / "podcast.json").write_text(json.dumps({
        "title": "My Podcast", "url": "https://example.com",
        "description": None, "image_url": None, "slug": "my-podcast",
    }))
    episodes_dir = podcast_dir / "episodes"
    episodes_dir.mkdir()
    (episodes_dir / "ep.json").write_text(json.dumps({
        "title": "Ep 1", "guid": "g1", "published": "2024-01-15",
        "audio_url": "https://example.com/ep.mp3", "duration": "120",
        "description": "ep", "slug": "ep-1",
        "status": {"download": {"completed_at": "2024-01-15T10:00:00",
                                 "result": {"path": "audio/ep.mp3"}}},
    }))
    audio_dir = podcast_dir / "audio"
    audio_dir.mkdir()
    (audio_dir / "ep.mp3").write_bytes(b"fake audio")

    return ann_dir, output_dir


class TestGroupConfigsByWhisper:
    def test_groups_by_whisper_settings(self):
        configs = [
            EvalConfig(name="a", whisper={"model": "base"}, llm={}, prompt="default", min_confidence=0.5),
            EvalConfig(name="b", whisper={"model": "base"}, llm={}, prompt="alt", min_confidence=0.5),
            EvalConfig(name="c", whisper={"model": "large"}, llm={}, prompt="default", min_confidence=0.5),
        ]
        groups = group_configs_by_whisper(configs)
        # "a" and "b" share whisper config, "c" is separate
        assert len(groups) == 2
        group_sizes = sorted(len(v) for v in groups.values())
        assert group_sizes == [1, 2]


class TestLoadPrompt:
    def test_loads_prompt_file(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "custom.txt").write_text("Find the ads.\n\nTranscript:\n")

        text = load_prompt("custom", prompts_dir)
        assert text == "Find the ads.\n\nTranscript:\n"

    def test_raises_on_missing_prompt(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            load_prompt("missing", prompts_dir)


class TestLoadRunConfig:
    def test_loads_yaml(self, tmp_path):
        config_path = tmp_path / "eval_config.yaml"
        config_path.write_text("""
output_dir: ./output
configs:
  - name: test-config
    whisper:
      model: base
      language: en
    llm:
      provider: anthropic
      model: claude-haiku-4-5-20251001
    prompt: default
    min_confidence: 0.5
""")
        run_config = load_run_config(config_path)
        assert run_config.output_dir == "./output"
        assert len(run_config.configs) == 1
        assert run_config.configs[0].name == "test-config"


class TestRunEval:
    def test_runs_eval_and_returns_scores(self, tmp_path):
        ann_dir, output_dir = _setup_annotation(tmp_path)

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "default.txt").write_text("Find ads.\n\nTranscript:\n")

        transcript_segments = [
            {"start": 0.0, "end": 10.0, "text": "Ad content"},
            {"start": 10.0, "end": 30.0, "text": "More ad"},
            {"start": 30.0, "end": 120.0, "text": "Main content"},
        ]
        predicted_ads = [
            AdSegment(start=0.0, end=30.0, confidence=0.9, detector="transcription", label="Pre-roll"),
        ]

        configs = [
            EvalConfig(name="test", whisper={"model": "base"}, llm={"provider": "anthropic", "model": "test"},
                       prompt="default", min_confidence=0.5),
        ]

        with patch("eval.run.transcribe", return_value=transcript_segments):
            with patch("eval.run.classify_with_prompt", return_value=predicted_ads):
                results = run_eval(
                    configs=configs,
                    annotations_dir=ann_dir,
                    output_dir=output_dir,
                    prompts_dir=prompts_dir,
                    results_dir=tmp_path / "results",
                )

        assert "test" in results
        assert results["test"].total_tp == 1
        assert results["test"].total_fp == 0
        assert results["test"].total_fn == 0
