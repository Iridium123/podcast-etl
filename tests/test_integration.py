"""Integration tests that run against real podcast feeds.

Only executed in GitHub Actions CI — skipped locally.
These tests make real HTTP requests: parse an RSS feed, download an episode,
tag the MP3, and stage it for seeding.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from mutagen.id3 import ID3

from podcast_etl.feed import parse_feed
from podcast_etl.models import StepStatus
from podcast_etl.pipeline import PipelineContext
from podcast_etl.steps.download import DownloadStep
from podcast_etl.steps.stage import StageStep
from podcast_etl.steps.tag import TagStep

pytestmark = pytest.mark.skipif(
    not os.environ.get("GITHUB_ACTIONS"),
    reason="Integration tests only run in GitHub Actions CI",
)

# Stable, well-known public feed (NPR Planet Money — ~20 min episodes)
FEED_URL = "https://feeds.npr.org/510289/podcast.xml"


def _record_status(episode, step_name, result):
    """Write a StepStatus entry so downstream steps can find prior output."""
    episode.status[step_name] = StepStatus(
        completed_at=datetime.now().isoformat(),
        result=result.data,
    )


def test_download_tag_stage(tmp_path):
    """Parse a real feed, download one episode, tag it, and stage it."""
    # --- Parse feed --------------------------------------------------------
    podcast = parse_feed(FEED_URL, output_dir=tmp_path)
    assert podcast.episodes, "Feed returned no episodes"
    assert podcast.title
    assert podcast.slug

    episode = next((ep for ep in podcast.episodes if ep.audio_url), None)
    assert episode is not None, "No episode with audio URL found"
    podcast.episodes = [episode]

    config = {"torrent_data_dir": str(tmp_path / "torrent-data")}
    context = PipelineContext(output_dir=tmp_path, podcast=podcast, config=config)

    # --- Download ----------------------------------------------------------
    dl_result = DownloadStep().process(episode, context)
    _record_status(episode, "download", dl_result)

    audio_path = context.podcast_dir / dl_result.data["path"]
    assert audio_path.exists()
    assert audio_path.suffix == ".mp3"
    assert dl_result.data["size_bytes"] > 0

    # --- Tag ---------------------------------------------------------------
    tag_result = TagStep().process(episode, context)
    _record_status(episode, "tag", tag_result)

    assert tag_result.data["release_date"]

    tags = ID3(audio_path)
    assert tags["TIT2"].text[0] == episode.title
    assert "TDRL" in tags
    assert "TPE1" in tags

    # --- Stage -------------------------------------------------------------
    stage_result = StageStep().process(episode, context)
    _record_status(episode, "stage", stage_result)

    staged = Path(stage_result.data["local_path"])
    assert staged.exists()
    assert staged.stat().st_size == audio_path.stat().st_size
