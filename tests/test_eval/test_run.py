"""Tests for the eval runner: config loading, labeling, scoring, orchestration."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from podcast_etl.detectors import AdSegment
from podcast_etl.labels import EpisodeRef, Labels, Provenance

from eval.datasets import load_dataset
from eval.run import (
    EvalConfig,
    RunConfig,
    _reuse_production_transcript,
    group_configs_by_whisper,
    label_dataset,
    load_run_config,
    run_eval,
    score_datasets,
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _setup_episode(output_dir, slug="my-podcast", episode_json="ep.json",
                   audio="ep.mp3", recorded_whisper=None, transcript=None):
    podcast_dir = output_dir / slug
    (podcast_dir / "episodes").mkdir(parents=True, exist_ok=True)
    (podcast_dir / "audio").mkdir(parents=True, exist_ok=True)
    (podcast_dir / "audio" / audio).write_bytes(b"fake audio")
    status = {"download": {"completed_at": "t", "result": {"path": f"audio/{audio}"}}}
    if recorded_whisper is not None:
        status["detect_ads"] = {"completed_at": "t", "result": {"whisper": recorded_whisper}}
    (podcast_dir / "episodes" / episode_json).write_text(json.dumps({
        "title": "Ep", "guid": "g", "published": "2024-01-15", "audio_url": "u",
        "duration": "120", "description": "d", "slug": "ep", "status": status,
    }))
    if transcript is not None:
        (podcast_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        stem = audio.rsplit(".", 1)[0]
        (podcast_dir / "transcripts" / f"{stem}.json").write_text(json.dumps(transcript))
    return podcast_dir


def _gold_labels(output_dir, slug="my-podcast", episode_json="ep.json",
                 segments=None, annotator="human", stem="ep"):
    gold_dir = output_dir / "gold"
    labels = Labels(
        episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
        audio_duration=120.0,
        segments=segments or [AdSegment(0.0, 30.0, 1.0, "gold", "Pre-roll")],
        provenance=Provenance(whisper={}, llm={}, annotator=annotator, created_at="t"),
    )
    labels.save(gold_dir / slug / "labels" / f"{stem}.json")
    return gold_dir


# ---------------------------------------------------------------------------
# config loading
# ---------------------------------------------------------------------------

class TestLoadRunConfig:
    def test_parses_configs_and_defaults(self, tmp_path):
        cfg = tmp_path / "eval_config.yaml"
        cfg.write_text(
            "gold: gold\n"
            "output_dir: ./output\n"
            "configs:\n"
            "  - name: haiku\n"
            "    whisper: {model: base, language: en}\n"
            "    llm: {provider: anthropic, model: claude-haiku-4-5-20251001}\n"
            "    prompt: default\n"
        )
        rc = load_run_config(cfg)
        assert rc.gold == "gold"
        assert rc.output_dir == "./output"
        assert len(rc.configs) == 1
        assert rc.configs[0].name == "haiku"
        assert rc.configs[0].whisper == {"model": "base", "language": "en"}
        # default allowed_annotators is human-only
        assert rc.allowed_annotators == ["human"]


# ---------------------------------------------------------------------------
# whisper grouping
# ---------------------------------------------------------------------------

class TestGroupConfigsByWhisper:
    def test_groups_by_content_affecting_fields(self):
        configs = [
            EvalConfig(name="a", whisper={"model": "base", "api_key": "k1"}, llm={}, prompt="default"),
            EvalConfig(name="b", whisper={"model": "base", "api_key": "k2"}, llm={}, prompt="alt"),
            EvalConfig(name="c", whisper={"model": "large"}, llm={}, prompt="default"),
        ]
        groups = group_configs_by_whisper(configs)
        # a and b share content-affecting whisper (api_key ignored); c separate
        assert sorted(len(v) for v in groups.values()) == [1, 2]


# ---------------------------------------------------------------------------
# production transcript reuse
# ---------------------------------------------------------------------------

class TestReuseProductionTranscript:
    def test_reuses_when_whisper_matches(self, tmp_path):
        from eval.resolve import resolve_episode
        _setup_episode(tmp_path, recorded_whisper={"model": "base", "language": "en"},
                       transcript=[{"start": 0.0, "end": 5.0, "text": "hi"}])
        resolved = resolve_episode(EpisodeRef("my-podcast", "ep.json"), tmp_path)
        reused = _reuse_production_transcript(resolved, {"model": "base", "language": "en"})
        assert reused == [{"start": 0.0, "end": 5.0, "text": "hi"}]

    def test_none_when_whisper_differs(self, tmp_path):
        from eval.resolve import resolve_episode
        _setup_episode(tmp_path, recorded_whisper={"model": "large", "language": "en"},
                       transcript=[{"start": 0.0, "end": 5.0, "text": "hi"}])
        resolved = resolve_episode(EpisodeRef("my-podcast", "ep.json"), tmp_path)
        assert _reuse_production_transcript(resolved, {"model": "base", "language": "en"}) is None

    def test_none_when_no_transcript(self, tmp_path):
        from eval.resolve import resolve_episode
        _setup_episode(tmp_path, recorded_whisper={"model": "base"}, transcript=None)
        resolved = resolve_episode(EpisodeRef("my-podcast", "ep.json"), tmp_path)
        assert _reuse_production_transcript(resolved, {"model": "base"}) is None


# ---------------------------------------------------------------------------
# scoring two datasets
# ---------------------------------------------------------------------------

class TestScoreDatasets:
    def _pred(self, output_dir, segments, slug="my-podcast", episode_json="ep.json", stem="ep"):
        pred_dir = output_dir / "preds"
        Labels(
            episode_ref=EpisodeRef(podcast_slug=slug, episode_json=episode_json),
            audio_duration=120.0, segments=segments,
            provenance=Provenance(whisper={}, llm={}, annotator="claude-haiku-4-5-20251001", created_at="t"),
        ).save(pred_dir / slug / "labels" / f"{stem}.json")
        return pred_dir

    def test_scores_intersection(self, tmp_path):
        gold = _gold_labels(tmp_path, segments=[AdSegment(0.0, 30.0, 1.0, "gold", "ad")])
        preds = self._pred(tmp_path, [AdSegment(0.0, 30.0, 0.9, "transcription", "ad")])
        agg = score_datasets(preds, gold, allowed_annotators=["human"])
        assert agg.total_tp == 1
        assert agg.precision == 1.0
        assert agg.episode_count == 1

    def test_filters_gold_by_annotator(self, tmp_path):
        # gold annotated by a model, not human -> excluded under human-only filter
        gold = _gold_labels(tmp_path, annotator="claude-sonnet-4-6")
        preds = self._pred(tmp_path, [AdSegment(0.0, 30.0, 0.9, "transcription", "ad")])
        agg = score_datasets(preds, gold, allowed_annotators=["human"])
        assert agg.episode_count == 0

    def test_empty_allowed_accepts_all_annotators(self, tmp_path):
        gold = _gold_labels(tmp_path, annotator="claude-sonnet-4-6")
        preds = self._pred(tmp_path, [AdSegment(0.0, 30.0, 0.9, "transcription", "ad")])
        agg = score_datasets(preds, gold, allowed_annotators=[])
        assert agg.episode_count == 1

    def test_gold_without_prediction_is_skipped(self, tmp_path):
        gold = _gold_labels(tmp_path)
        empty_pred = tmp_path / "preds"
        (empty_pred / "x" / "labels").mkdir(parents=True)
        agg = score_datasets(empty_pred, gold, allowed_annotators=["human"])
        assert agg.episode_count == 0


# ---------------------------------------------------------------------------
# label_dataset
# ---------------------------------------------------------------------------

class TestLabelDataset:
    def test_writes_labels_reusing_production_transcript(self, tmp_path):
        _setup_episode(tmp_path, recorded_whisper={"model": "base", "language": "en"},
                       transcript=[{"start": 0.0, "end": 5.0, "text": "ad copy"}])
        config = EvalConfig(name="haiku", whisper={"model": "base", "language": "en"},
                            llm={"model": "claude-haiku-4-5-20251001"}, prompt="default")
        dataset_dir = tmp_path / "datasets" / "haiku"
        refs = [EpisodeRef("my-podcast", "ep.json")]

        predicted = [AdSegment(0.0, 5.0, 0.9, "transcription", "Pre-roll")]
        with patch("eval.run.transcribe") as mock_transcribe, \
             patch("eval.run.classify_to_segments", return_value=predicted), \
             patch("eval.run._audio_duration", return_value=120.0):
            written = label_dataset(config, refs, tmp_path, dataset_dir,
                                    prompt_text="PROMPT", client=None, transcript_cache={})

        mock_transcribe.assert_not_called()  # reused production transcript
        assert len(written) == 1
        dataset = load_dataset(dataset_dir)
        labels = dataset["my-podcast/ep.json"]
        assert labels.segments[0].label == "Pre-roll"
        assert labels.provenance.annotator == "claude-haiku-4-5-20251001"
        assert labels.audio_duration == 120.0

    def test_transcribes_when_no_reuse(self, tmp_path):
        _setup_episode(tmp_path, recorded_whisper=None, transcript=None)
        config = EvalConfig(name="haiku", whisper={"model": "base"},
                            llm={"model": "m"}, prompt="default")
        dataset_dir = tmp_path / "datasets" / "haiku"
        refs = [EpisodeRef("my-podcast", "ep.json")]

        with patch("eval.run.transcribe", return_value=[{"start": 0.0, "end": 5.0, "text": "x"}]) as mock_t, \
             patch("eval.run.classify_to_segments", return_value=[]), \
             patch("eval.run._audio_duration", return_value=120.0):
            label_dataset(config, refs, tmp_path, dataset_dir,
                          prompt_text="PROMPT", client=None, transcript_cache={})
        mock_t.assert_called_once()


# ---------------------------------------------------------------------------
# run_eval orchestration
# ---------------------------------------------------------------------------

class TestRunEval:
    def test_labels_then_scores_each_config(self, tmp_path):
        _setup_episode(tmp_path, recorded_whisper={"model": "base", "language": "en"},
                       transcript=[{"start": 0.0, "end": 30.0, "text": "ad"}])
        gold_dir = _gold_labels(tmp_path, segments=[AdSegment(0.0, 30.0, 1.0, "gold", "ad")])

        configs = [EvalConfig(name="haiku", whisper={"model": "base", "language": "en"},
                              llm={"model": "claude-haiku-4-5-20251001"}, prompt="default")]
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "default.txt").write_text("classify ads")
        results_dir = tmp_path / "results"
        datasets_dir = tmp_path / "datasets"

        predicted = [AdSegment(0.0, 30.0, 0.9, "transcription", "ad")]
        with patch("eval.run.classify_to_segments", return_value=predicted), \
             patch("eval.run._audio_duration", return_value=120.0), \
             patch("eval.run.build_llm_client", return_value=None):
            results = run_eval(
                configs=configs, output_dir=tmp_path, gold_dir=gold_dir,
                datasets_dir=datasets_dir, prompts_dir=prompts_dir,
                results_dir=results_dir, allowed_annotators=["human"], timestamp="2026-05-31T00-00-00",
            )

        assert results["haiku"].total_tp == 1
        assert results["haiku"].precision == 1.0
        # results file written
        assert (results_dir / "2026-05-31T00-00-00-haiku.json").exists()

    def test_skips_labeling_annotator_excluded_gold(self, tmp_path):
        # Gold annotated by a model, but the run only counts human gold: nothing
        # should be labeled (labeling is billable) and nothing scored.
        _setup_episode(tmp_path, recorded_whisper={"model": "base", "language": "en"},
                       transcript=[{"start": 0.0, "end": 30.0, "text": "ad"}])
        gold_dir = _gold_labels(tmp_path, annotator="claude-sonnet-4-6")
        configs = [EvalConfig(name="haiku", whisper={"model": "base", "language": "en"},
                              llm={"model": "m"}, prompt="default")]
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "default.txt").write_text("classify")

        with patch("eval.run.classify_to_segments") as mock_classify, \
             patch("eval.run.build_llm_client", return_value=None):
            results = run_eval(
                configs=configs, output_dir=tmp_path, gold_dir=gold_dir,
                datasets_dir=tmp_path / "ds", prompts_dir=prompts_dir,
                results_dir=tmp_path / "r", allowed_annotators=["human"], timestamp="t",
            )

        mock_classify.assert_not_called()
        assert results["haiku"].episode_count == 0

    def test_duplicate_config_names_raise(self, tmp_path):
        gold_dir = _gold_labels(tmp_path)
        configs = [
            EvalConfig(name="dup", whisper={}, llm={}, prompt="default"),
            EvalConfig(name="dup", whisper={}, llm={}, prompt="default"),
        ]
        with pytest.raises(ValueError, match="Duplicate config names"):
            run_eval(configs=configs, output_dir=tmp_path, gold_dir=gold_dir,
                     datasets_dir=tmp_path / "ds", prompts_dir=tmp_path,
                     results_dir=tmp_path / "r", timestamp="t")
