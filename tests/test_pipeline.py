"""Tests for pipeline.py: Pipeline step execution and skipping logic."""
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcast_etl.models import Episode, Podcast, StepStatus
from podcast_etl.pipeline import Pipeline, PipelineContext, StepResult


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


def test_pipeline_continues_after_step_exception(tmp_path: Path):
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
