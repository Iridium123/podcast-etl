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
    overwrite: bool = False

    @property
    def podcast_dir(self) -> Path:
        return self.podcast.podcast_dir(self.output_dir)

    @property
    def effective_title(self) -> str:
        return self.config.get("title_override") or self.podcast.title


@dataclass
class StepResult:
    data: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    name: str

    def process(self, episode: Episode, context: PipelineContext) -> StepResult: ...


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overrides* into *base*.

    Dict values are merged recursively so the override only needs to specify
    the keys it wants to change at any depth.  Non-dict values (scalars, lists)
    are replaced outright.  Raises TypeError if a key is a dict in one side
    and a non-dict in the other.
    """
    merged: dict[str, Any] = {}
    for key in base.keys() | overrides.keys():
        in_base = key in base
        in_over = key in overrides
        if in_base and in_over:
            base_val = base[key]
            over_val = overrides[key]
            if isinstance(base_val, dict) and isinstance(over_val, dict):
                merged[key] = deep_merge(base_val, over_val)
            elif isinstance(base_val, dict) != isinstance(over_val, dict):
                raise TypeError(
                    f"Type mismatch for key {key!r}: "
                    f"base is {type(base_val).__name__}, "
                    f"override is {type(over_val).__name__}"
                )
            else:
                merged[key] = over_val
        elif in_over:
            merged[key] = overrides[key]
        else:
            merged[key] = base[key]
    return merged


def resolve_feed_config(defaults: dict[str, Any], feed: dict[str, Any]) -> dict[str, Any]:
    """Merge global defaults with per-feed overrides using deep merge.

    Use this rather than calling ``deep_merge`` directly so any future
    pre/post-processing (e.g. env-var expansion) can be added in one place.
    """
    return deep_merge(defaults, feed)


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
            logger.debug("[%d/%d] %s", i, total, episode.title or episode.slug)
            for step in steps:
                if overwrite:
                    episode.status.pop(step.name, None)
                if step.name in episode.status and episode.status[step.name] is not None:
                    logger.debug("  skip %s", step.name)
                    continue
                try:
                    logger.debug("  -> %s", step.name)
                    result = step.process(episode, self.context)
                    episode.status[step.name] = StepStatus(
                        completed_at=datetime.now().isoformat(),
                        result=result.data,
                    )
                    episode.save(self.context.podcast_dir, self.context.podcast.title)
                    logger.debug("  done %s", step.name)
                except Exception:
                    logger.exception("  %s failed for %s", step.name, episode.slug)
                    logger.debug("  stopping remaining steps for %s", episode.slug)
                    break
