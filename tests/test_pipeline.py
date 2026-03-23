"""Tests for pipeline.py: Pipeline step execution and skipping logic."""
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import Pipeline, PipelineContext, StepResult, deep_merge, merge_config


# --- Helpers ---

def _make_episode(slug="ep-1", status=None) -> Episode:
    return Episode(
        title="Episode 1",
        guid="guid-1",
        published=None,
        audio_url="https://example.com/ep.mp3",
        duration=None,
        description=None,
        slug=slug,
        status=status or {},
    )


def _make_context(tmp_path: Path) -> PipelineContext:
    podcast = Podcast(
        title="Test Podcast",
        url="https://example.com/feed.xml",
        description=None,
        image_url=None,
        slug="test-podcast",
    )
    return PipelineContext(output_dir=tmp_path, podcast=podcast)


@dataclass
class FakeStep:
    name: str = "fake"
    call_count: int = field(default=0, compare=False)
    return_data: dict = field(default_factory=dict)

    def process(self, episode: Episode, context: PipelineContext) -> StepResult:
        self.call_count += 1
        return StepResult(data=self.return_data)


# --- Tests ---

def test_pipeline_runs_step_for_episode(tmp_path: Path):
    step = FakeStep()
    ep = _make_episode()
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert step.call_count == 1


def test_pipeline_skips_already_completed_step(tmp_path: Path):
    step = FakeStep()
    completed = StepStatus(completed_at="2024-01-01T00:00:00", result={})
    ep = _make_episode(status={"fake": completed})
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert step.call_count == 0


def test_pipeline_saves_status_after_step(tmp_path: Path):
    step = FakeStep(return_data={"key": "value"})
    ep = _make_episode()
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep])

    assert "fake" in ep.status
    assert ep.status["fake"].result == {"key": "value"}
    # Verify it was written to disk
    saved_path = ctx.podcast_dir / "episodes" / "Test Podcast - unknown-date - Episode 1.json"
    assert saved_path.exists()


def test_pipeline_step_filter_runs_only_named_step(tmp_path: Path):
    step_a = FakeStep(name="step-a")
    step_b = FakeStep(name="step-b")
    ep = _make_episode()
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step_a, step_b], context=ctx).run([ep], step_filter="step-a")

    assert step_a.call_count == 1
    assert step_b.call_count == 0


def test_pipeline_step_filter_unknown_raises(tmp_path: Path):
    step = FakeStep()
    ep = _make_episode()
    ctx = _make_context(tmp_path)

    with pytest.raises(ValueError, match="not found in pipeline"):
        Pipeline(steps=[step], context=ctx).run([ep], step_filter="nonexistent")


def test_pipeline_overwrite_reruns_completed_step(tmp_path: Path):
    step = FakeStep()
    completed = StepStatus(completed_at="2024-01-01T00:00:00", result={})
    ep = _make_episode(status={"fake": completed})
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run([ep], overwrite=True)

    assert step.call_count == 1


def test_pipeline_runs_multiple_episodes(tmp_path: Path):
    step = FakeStep()
    episodes = [_make_episode(slug=f"ep-{i}") for i in range(3)]
    ctx = _make_context(tmp_path)

    Pipeline(steps=[step], context=ctx).run(episodes)

    assert step.call_count == 3


def test_pipeline_continues_to_next_episode_after_failure(tmp_path: Path):
    """A failing step should be caught; subsequent episodes still run."""
    call_log = []

    class BoomStep:
        name = "boom"

        def process(self, episode: Episode, context: PipelineContext) -> StepResult:
            call_log.append(episode.slug)
            if episode.slug == "ep-0":
                raise RuntimeError("simulated failure")
            return StepResult()

    episodes = [_make_episode(slug=f"ep-{i}") for i in range(3)]
    ctx = _make_context(tmp_path)

    Pipeline(steps=[BoomStep()], context=ctx).run(episodes)

    # All three episodes were attempted despite the first failing
    assert call_log == ["ep-0", "ep-1", "ep-2"]
    # Only the successful ones have status set
    assert "boom" not in episodes[0].status
    assert "boom" in episodes[1].status
    assert "boom" in episodes[2].status


def test_pipeline_stops_remaining_steps_on_failure(tmp_path: Path):
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

    ep = _make_episode()
    ctx = _make_context(tmp_path)

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


def test_pipeline_context_podcast_dir(tmp_path: Path):
    ctx = _make_context(tmp_path)
    assert ctx.podcast_dir == tmp_path / "test-podcast"


# --- merge_config ---

class TestMergeConfig:
    def test_global_only(self):
        assert merge_config({"a": 1, "b": 2}, {}) == {"a": 1, "b": 2}

    def test_feed_overrides_scalar(self):
        assert merge_config({"a": 1}, {"a": 99}) == {"a": 99}

    def test_feed_adds_new_key(self):
        assert merge_config({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_dicts_merged(self):
        result = merge_config(
            {"llm": {"provider": "anthropic", "model": "sonnet"}},
            {"llm": {"model": "haiku"}},
        )
        assert result == {"llm": {"provider": "anthropic", "model": "haiku"}}

    def test_both_empty(self):
        assert merge_config({}, {}) == {}

    def test_feed_replaces_scalar_with_scalar(self):
        result = merge_config({"min_confidence": 0.5}, {"min_confidence": 0.8})
        assert result == {"min_confidence": 0.8}

    def test_feed_adds_new_dict_key(self):
        result = merge_config({}, {"llm": {"model": "haiku"}})
        assert result == {"llm": {"model": "haiku"}}

    def test_type_mismatch_dict_vs_scalar_raises(self):
        with pytest.raises(TypeError, match="Type mismatch for key 'llm'"):
            merge_config({"llm": {"model": "sonnet"}}, {"llm": "haiku"})

    def test_type_mismatch_scalar_vs_dict_raises(self):
        with pytest.raises(TypeError, match="Type mismatch for key 'model'"):
            merge_config({"model": "sonnet"}, {"model": {"name": "haiku"}})


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
