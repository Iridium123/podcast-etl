from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from podcast_etl.models import Episode, Podcast, StepStatus

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    output_dir: Path
    podcast: Podcast
    config: dict[str, Any] = field(default_factory=dict)
    feed_config: dict[str, Any] = field(default_factory=dict)
    overwrite: bool = False

    @property
    def podcast_dir(self) -> Path:
        return self.podcast.podcast_dir(self.output_dir)

    @property
    def effective_title(self) -> str:
        return self.feed_config.get("title_override") or self.podcast.title


@dataclass
class StepResult:
    data: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    name: str

    def process(self, episode: Episode, context: PipelineContext) -> StepResult: ...


STEP_REGISTRY: dict[str, Step] = {}


def register_step(step: Step) -> None:
    STEP_REGISTRY[step.name] = step


def get_step(name: str) -> Step:
    if name not in STEP_REGISTRY:
        raise ValueError(f"Unknown step: {name!r}. Available: {list(STEP_REGISTRY)}")
    return STEP_REGISTRY[name]


class Pipeline:
    def __init__(self, steps: list[Step], context: PipelineContext) -> None:
        self.steps = steps
        self.context = context

    def run(self, episodes: list[Episode], step_filter: str | None = None, overwrite: bool = False) -> None:
        steps = self.steps
        if step_filter:
            steps = [s for s in steps if s.name == step_filter]
            if not steps:
                raise ValueError(f"Step {step_filter!r} not found in pipeline")

        total = len(episodes)
        for i, episode in enumerate(episodes, 1):
            logger.info("[%d/%d] %s", i, total, episode.title or episode.slug)
            for step in steps:
                if overwrite:
                    episode.status.pop(step.name, None)
                if step.name in episode.status and episode.status[step.name] is not None:
                    logger.debug("  skip %s", step.name)
                    continue
                try:
                    logger.info("  -> %s", step.name)
                    result = step.process(episode, self.context)
                    episode.status[step.name] = StepStatus(
                        completed_at=datetime.now().isoformat(),
                        result=result.data,
                    )
                    episode.save(self.context.podcast_dir, self.context.podcast.title)
                    logger.debug("  done %s", step.name)
                except Exception:
                    logger.exception("  %s failed for %s", step.name, episode.slug)
                    break
