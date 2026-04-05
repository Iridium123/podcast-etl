"""Tests for pipeline.py: Pipeline step execution and skipping logic."""
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcast_etl.models import Episode, StepStatus
from podcast_etl.pipeline import Pipeline, PipelineContext, StepResult, deep_merge, resolve_feed_config


# --- Helpers ---


@dataclass
class FakeStep:
    name: str = "fake"
    call_count: int = field(default=0, compare=False)
    return_data: dict = field(default_factory=dict)

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        self.call_count += 1
        return StepResult(data=self.return_data)


# --- Tests ---

def test_pipeline_runs_step_for_episode(tmp_path, make_episode, make_context):
    step = FakeStep()
    ep = make_episode(published=None)
    ctx = make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert step.call_count == 1


def test_pipeline_skips_already_completed_step(tmp_path, make_episode, make_context):
    step = FakeStep()
    completed = StepStatus(completed_at="2024-01-01T00:00:00", result={})
    ep = make_episode(published=None, status={"fake": completed})
    ctx = make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert step.call_count == 0


def test_pipeline_saves_status_after_step(tmp_path, make_episode, make_context):
    step = FakeStep(return_data={"key": "value"})
    ep = make_episode(published=None)
    ctx = make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert "fake" in ep.status
    assert ep.status["fake"].result == {"key": "value"}
    # Verify it was written to disk
    episodes_dir = ctx.podcast_dir / "episodes"
    assert episodes_dir.exists()
    assert len(list(episodes_dir.glob("*.json"))) == 1


def test_pipeline_step_filter_runs_only_named_step(tmp_path, make_episode, make_context):
    step_a = FakeStep(name="step-a")
    step_b = FakeStep(name="step-b")
    ep = make_episode(published=None)
    ctx = make_context(tmp_path)

    Pipeline(steps=[step_a, step_b], context=ctx).run([ep], step_filter="step-a")

    assert step_a.call_count == 1
    assert step_b.call_count == 0


def test_pipeline_step_filter_unknown_raises(tmp_path, make_episode, make_context):
    step = FakeStep()
    ep = make_episode(published=None)
    ctx = make_context(tmp_path)

    with pytest.raises(ValueError, match="not found in pipeline"):
        Pipeline(steps=[step], context=ctx).run([ep], step_filter="nonexistent")


def test_pipeline_overwrite_reruns_completed_step(tmp_path, make_episode, make_context):
    step = FakeStep()
    completed = StepStatus(completed_at="2024-01-01T00:00:00", result={})
    ep = make_episode(published=None, status={"fake": completed})
    ctx = make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep], overwrite=True)

    assert step.call_count == 1


def test_pipeline_runs_multiple_episodes(tmp_path, make_episode, make_context):
    step = FakeStep()
    episodes = [make_episode(published=None, slug=f"ep-{i}") for i in range(3)]
    ctx = make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run(episodes)

    assert step.call_count == 3


def test_pipeline_continues_to_next_episode_after_failure(tmp_path, make_episode, make_context):
    """A failing step should be caught; subsequent episodes still run."""
    call_log = []

    class BoomStep:
        name = "boom"

        def process(self, episode: Episode, context: PipelineContext) -> StepResult:
            call_log.append(episode.slug)
            if episode.slug == "ep-0":
                raise RuntimeError("simulated failure")
            return StepResult()

    episodes = [make_episode(published=None, slug=f"ep-{i}") for i in range(3)]
    ctx = make_context(tmp_path)

    Pipeline(steps=[BoomStep()], context=ctx).run(episodes)

    # All three episodes were attempted despite the first failing
    assert call_log == ["ep-0", "ep-1", "ep-2"]
    # Only the successful ones have status set
    assert "boom" not in episodes[0].status
    assert "boom" in episodes[1].status
    assert "boom" in episodes[2].status


def test_pipeline_stops_remaining_steps_on_failure(tmp_path, make_episode, make_context):
    """A failing step should prevent later steps from running for that episode."""
    step_a_log = []
    step_b_log = []

    class FailingStepA:
        name = "step-a"

        def process(self, episode: Episode, context: PipelineContext) -> StepResult:
            step_a_log.append(episode.slug)
            raise RuntimeError("step-a failed")

    class StepB:
        name = "step-b"

        def process(self, episode: Episode, context: PipelineContext) -> StepResult:
            step_b_log.append(episode.slug)
            return StepResult()

    ep = make_episode(published=None)
    ctx = make_context(tmp_path)

    Pipeline(steps=[FailingStepA(), StepB()], context=ctx).run([ep])

    assert step_a_log == ["ep-1"]
    assert step_b_log == []


def test_get_step_unknown_raises():
    from podcast_etl.pipeline import get_step
    with pytest.raises(ValueError, match="Unknown step"):
        get_step("nonexistent-step-xyz")


def test_register_and_get_step(tmp_path: Path):
    from podcast_etl.pipeline import STEP_REGISTRY, get_step, register_step

    step = FakeStep(name="test-register-step")
    register_step(step)
    try:
        assert get_step("test-register-step") is step
    finally:
        STEP_REGISTRY.pop("test-register-step", None)


def test_pipeline_context_podcast_dir(tmp_path, make_context):
    ctx = make_context(tmp_path)
    assert ctx.podcast_dir == tmp_path / "test-podcast"


# --- deep_merge ---

class TestDeepMerge:
    def test_base_only(self):
        assert deep_merge({"a": 1, "b": 2}, {}) == {"a": 1, "b": 2}

    def test_override_scalar(self):
        assert deep_merge({"a": 1}, {"a": 99}) == {"a": 99}

    def test_adds_new_key(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_both_empty(self):
        assert deep_merge({}, {}) == {}

    def test_nested_dicts_merged_recursively(self):
        result = deep_merge(
            {"llm": {"provider": "anthropic", "model": "sonnet"}},
            {"llm": {"model": "haiku"}},
        )
        assert result == {"llm": {"provider": "anthropic", "model": "haiku"}}

    def test_three_levels_deep(self):
        result = deep_merge(
            {"ad_detection": {"llm": {"provider": "anthropic", "model": "sonnet"}, "min_confidence": 0.5}},
            {"ad_detection": {"llm": {"model": "haiku"}}},
        )
        assert result == {"ad_detection": {"llm": {"provider": "anthropic", "model": "haiku"}, "min_confidence": 0.5}}

    def test_list_replaced_not_merged(self):
        result = deep_merge({"pipeline": ["a", "b"]}, {"pipeline": ["c"]})
        assert result == {"pipeline": ["c"]}

    def test_override_adds_new_nested_key(self):
        result = deep_merge({"a": {"x": 1}}, {"a": {"y": 2}})
        assert result == {"a": {"x": 1, "y": 2}}

    def test_type_mismatch_dict_vs_scalar_raises(self):
        with pytest.raises(TypeError, match="Type mismatch for key 'llm'"):
            deep_merge({"llm": {"model": "sonnet"}}, {"llm": "haiku"})

    def test_type_mismatch_scalar_vs_dict_raises(self):
        with pytest.raises(TypeError, match="Type mismatch for key 'model'"):
            deep_merge({"model": "sonnet"}, {"model": {"name": "haiku"}})

    def test_does_not_mutate_inputs(self):
        base = {"a": {"x": 1}}
        overrides = {"a": {"y": 2}}
        deep_merge(base, overrides)
        assert base == {"a": {"x": 1}}
        assert overrides == {"a": {"y": 2}}


# --- resolve_feed_config ---

class TestResolveFeedConfig:
    def test_defaults_only(self):
        defaults = {"output_dir": "./output", "pipeline": ["download"]}
        result = resolve_feed_config(defaults, {"url": "https://example.com/rss"})
        assert result["output_dir"] == "./output"
        assert result["pipeline"] == ["download"]
        assert result["url"] == "https://example.com/rss"

    def test_feed_overrides_scalar(self):
        defaults = {"pipeline": ["download"]}
        feed = {"url": "https://example.com/rss", "pipeline": ["download", "tag"]}
        result = resolve_feed_config(defaults, feed)
        assert result["pipeline"] == ["download", "tag"]

    def test_deep_merges_nested_dicts(self):
        defaults = {"ad_detection": {"llm": {"provider": "anthropic", "model": "sonnet"}, "min_confidence": 0.5}}
        feed = {"url": "https://example.com/rss", "ad_detection": {"llm": {"model": "haiku"}}}
        result = resolve_feed_config(defaults, feed)
        assert result["ad_detection"] == {
            "llm": {"provider": "anthropic", "model": "haiku"},
            "min_confidence": 0.5,
        }

    def test_empty_defaults(self):
        result = resolve_feed_config({}, {"url": "https://example.com/rss", "pipeline": ["download"]})
        assert result["pipeline"] == ["download"]

    def test_empty_feed(self):
        defaults = {"pipeline": ["download"]}
        result = resolve_feed_config(defaults, {})
        assert result["pipeline"] == ["download"]
