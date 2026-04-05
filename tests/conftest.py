"""Shared test fixtures: factory functions for Episode, Podcast, and PipelineContext."""

from pathlib import Path

import pytest

from podcast_etl.models import Episode, Podcast
from podcast_etl.pipeline import PipelineContext


@pytest.fixture
def make_episode():
    """Factory fixture for creating Episode instances with sensible defaults."""

    def _make_episode(**kwargs) -> Episode:
        defaults = dict(
            title="Episode 1",
            guid="guid-1",
            published="Mon, 01 Jan 2024 00:00:00 +0000",
            audio_url="https://example.com/ep.mp3",
            duration=None,
            description=None,
            slug="ep-1",
            status={},
        )
        defaults.update(kwargs)
        return Episode(**defaults)

    return _make_episode


@pytest.fixture
def make_podcast():
    """Factory fixture for creating Podcast instances with sensible defaults."""

    def _make_podcast(**kwargs) -> Podcast:
        defaults = dict(
            title="Test Podcast",
            url="https://example.com/feed.xml",
            description=None,
            image_url=None,
            slug="test-podcast",
        )
        defaults.update(kwargs)
        return Podcast(**defaults)

    return _make_podcast


@pytest.fixture
def make_context(make_podcast):
    """Factory fixture for creating basic PipelineContext instances."""

    def _make_context(tmp_path: Path, **kwargs) -> PipelineContext:
        if "podcast" not in kwargs:
            kwargs["podcast"] = make_podcast()
        if "output_dir" not in kwargs:
            kwargs["output_dir"] = tmp_path
        return PipelineContext(**kwargs)

    return _make_context
